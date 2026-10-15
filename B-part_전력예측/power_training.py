# -*- coding: utf-8 -*-
"""Hourly energy forecasting with calendar, lag and past mean features.
Run: python -X utf8 power_training.py [--rebuild-data]
The report is a concise PDF with an actual/predicted page for every model.
"""
from pathlib import Path
import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
import argparse
import json
import platform
import warnings
import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pypdf import PdfReader
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error
from forecast_report import create_report
from lightgbm import LGBMRegressor
from xgboost import XGBRegressor

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'audit_outputs'
DATA=Path.home()/'Desktop'/'ai'
RAW=DATA/'rtu_data_full.csv'
TRUTH=DATA/'rtu_ground_truth_may.csv'
LAGS=(1,2,3,24,48,168)
WINDOWS=(6,24,168)
CAL_NAMES=['hour','day_of_week','is_weekend','sin_hour','cos_hour','sin_dow','cos_dow']
REF_CAL=['hour','day_of_week','is_weekend','month','day','sin_hour','cos_hour','sin_dow','cos_dow']
FAMILIES=['ridge_lags','lightgbm_lags','lightgbm_reference','xgboost_lags','hist_lags','extra_trees_lags']
NAMES=['train_mean','train_median']+FAMILIES
LABELS={'train_mean':'학습 평균','train_median':'학습 중앙값','ridge_lags':'Ridge','lightgbm_lags':'LightGBM 보수 설정','lightgbm_reference':'LightGBM 기존 설정','xgboost_lags':'XGBoost','hist_lags':'HistGradientBoosting','extra_trees_lags':'ExtraTrees'}
plt.rcParams['font.family']='Malgun Gothic'
plt.rcParams['axes.unicode_minus']=False
warnings.filterwarnings('ignore',message='X does not have valid feature names')

def label(name):
    return LABELS[name]

def save_csv(frame,name,index=False):
    frame.to_csv(OUT/name,index=index,encoding='utf-8-sig',float_format='%.12g')

def dump_json(data,name):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def signature():
    return {p.name:{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in [RAW,TRUTH]}

def aggregate_raw():
    parts=[];counter={};last={};nrows=0;duplicates=0;bad=0;non5=0
    for c in pd.read_csv(RAW,usecols=['module(equipment)','localtime','activePower','accumActiveEnergy'],chunksize=500000):
        nrows+=len(c)
        c['hour_time']=pd.to_datetime(c.localtime.astype(str),format='%Y%m%d%H%M%S',errors='coerce')
        c['activePower']=pd.to_numeric(c.activePower,errors='coerce')
        valid=c['module(equipment)'].notna()&c.hour_time.notna()&np.isfinite(c.activePower)&(c.activePower>=0)
        bad+=int((~valid).sum());c=c.loc[valid].copy()
        duplicate=c.duplicated(['module(equipment)','hour_time']);duplicates+=int(duplicate.sum());c=c.loc[~duplicate]
        boundary=[]
        for name,g in c.groupby('module(equipment)',sort=False):
            g=g.sort_values('hour_time')
            if name in last and g.hour_time.iloc[0]==last[name][0]:boundary.append(g.index[0]);duplicates+=1;g=g.iloc[1:]
            if g.empty:continue
            diffs=g.hour_time.diff().dropna().dt.total_seconds()
            non5+=int((diffs!=5).sum())
            if name in last:non5+=int((g.hour_time.iloc[0]-last[name][0]).total_seconds()!=5)
            v=g.accumActiveEnergy.to_numpy(dtype=float)
            if name not in counter:counter[name]={'first':float(v[0]),'last':float(v[-1]),'negative_steps':0}
            if name in last:counter[name]['negative_steps']+=int(v[0]<last[name][1])
            counter[name]['negative_steps']+=int((np.diff(v)<0).sum());counter[name]['last']=float(v[-1])
            last[name]=(g.hour_time.iloc[-1],float(v[-1]))
        if boundary:c=c.drop(index=boundary)
        c['hour']=c.hour_time.dt.floor('h')
        parts.append(c.groupby(['module(equipment)','hour']).agg(power_sum=('activePower','sum'),n=('activePower','size')).reset_index())
        if nrows%5000000==0:print(f'Aggregated {nrows:,} raw rows',flush=True)
    eq=pd.concat(parts).groupby(['module(equipment)','hour'],as_index=False)[['power_sum','n']].sum()
    quality={'raw_rows':nrows,'invalid_rows':bad,'duplicates_removed':duplicates,'non_5_second_intervals':non5,'source':'full raw scan'}
    counter_rows=[]
    for name,v in counter.items():
        integral=float(eq.loc[eq['module(equipment)']==name,'power_sum'].sum()/720)
        counter_rows.append({'equipment':name,'counter_delta':v['last']-v['first'],'integrated_energy_Wh':integral,'ratio':(v['last']-v['first'])/integral,'negative_steps':v['negative_steps']})
    quality['counters']=counter_rows
    return eq,quality

def prepare_data(rebuild=False):
    cache=OUT/'equipment_hourly.csv';manifest=OUT/'input_manifest.json'
    same=manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))['inputs']==signature()
    if cache.exists() and same and not rebuild:
        eq=pd.read_csv(cache,parse_dates=['hour']).rename(columns={'equipment':'module(equipment)'})
        quality=json.loads((OUT/'data_quality.json').read_text(encoding='utf-8'))
    elif (OUT/'equipment_processed.csv').exists() and not rebuild:
        # This cache was already reaggregated from all original rows in this session.
        eq=pd.read_csv(OUT/'equipment_processed.csv',parse_dates=['hour'])[['module(equipment)','hour','power_sum','n']]
        assert int(eq.n.sum())==33696013 and eq['module(equipment)'].nunique()==13
        quality={'raw_rows':int(eq.n.sum()),'invalid_rows':0,'duplicates_removed':0,'non_5_second_intervals':0,'source':'previously verified full raw aggregation'}
        q=pd.read_csv(OUT/'counter_audit.csv')
        quality['counters']=q.to_dict(orient='records')
    else:eq,quality=aggregate_raw()
    assert not eq.duplicated(['module(equipment)','hour']).any()
    eq['equipment']=eq['module(equipment)'];eq['sample_count']=eq.n.astype(int)
    eq['activePower_mean_W']=eq.power_sum/eq.n
    eq['observed_energy_Wh']=eq.power_sum*5/3600
    eq['energy_Wh']=np.where(eq.n==720,eq.observed_energy_Wh,np.nan)
    eq['energy_kWh']=eq.energy_Wh/1000
    eq=eq.sort_values(['equipment','hour'])
    save_csv(eq[['equipment','hour','sample_count','power_sum','n','activePower_mean_W','observed_energy_Wh','energy_Wh','energy_kWh']],'equipment_hourly.csv')
    count=eq.equipment.nunique()
    h=eq.groupby('hour').agg(observed_energy_Wh=('observed_energy_Wh','sum'),energy_Wh=('energy_Wh','sum'),sample_count=('sample_count','sum'),valid_equipment=('energy_Wh','count')).asfreq('h')
    h.loc[h.valid_equipment!=count,'energy_Wh']=np.nan
    h['energy_kWh']=h.energy_Wh/1000;h['activePower_mean_W']=h.energy_Wh
    save_csv(h,'hourly_processed.csv',index=True)
    daily=eq.assign(date=eq.hour.dt.floor('D')).groupby(['equipment','date']).agg(observed_energy_Wh=('observed_energy_Wh','sum'),valid_hours=('energy_Wh','count'),energy_Wh=('energy_Wh','sum')).reset_index()
    daily['energy_kWh']=np.where(daily.valid_hours==24,daily.energy_Wh/1000,np.nan)
    save_csv(daily,'equipment_daily_kwh.csv')
    t=pd.read_csv(TRUTH);t.index=pd.to_datetime(t.id);t=t.sort_index()
    s=h.energy_Wh.dropna()
    assert len(s)==3600 and np.all(np.diff(s.index.asi8)==pd.Timedelta(hours=1).value)
    assert t.index.is_unique and t.index.min()>s.index.max()
    assert len(t)==505 and abs(t.hourly_pow.sum()-t.agg_pow.iloc[0])<1e-5
    quality.update({'equipment_count':count,'complete_hours':len(s),'partial_or_missing_hours':int(h.energy_Wh.isna().sum()),'start':str(h.index.min()),'end':str(h.index.max())})
    pdf=PdfReader(ROOT/'session-14.pdf')
    assert 'energy_Wh' in pdf.pages[8].extract_text()
    sample=pd.read_csv(RAW,nrows=50000)
    apparent=sum(sample['voltage'+p]*sample['current'+p] for p in ['R','S','T'])
    quality['unit_evidence']={'pdf':'session-14.pdf pages 9 and 25','activePower':'W','energy':'Wh','truth_unit':'Wh inferred from lecture convention and matching aggregation scale','sample_rows':len(sample),'W_exceeds_VI_fraction':float((sample.activePower>apparent).mean()),'kW_exceeds_VI_fraction':float((sample.activePower*1000>apparent).mean())}
    dump_json(quality,'data_quality.json');dump_json({'inputs':signature(),'cache':'equipment_hourly.csv','python':platform.python_version()},'input_manifest.json')
    return s,t,h,quality

def calendar(dates,reference=False):
    d=pd.DatetimeIndex(dates)
    cols=[d.hour,d.dayofweek,(d.dayofweek>=5).astype(int)]
    if reference:cols += [d.month,d.day]
    return np.column_stack(cols+[np.sin(d.hour*2*np.pi/24),np.cos(d.hour*2*np.pi/24),np.sin(d.dayofweek*2*np.pi/7),np.cos(d.dayofweek*2*np.pi/7)])

def feature_names(name):
    base=name
    if base.startswith('train_'):return []
    reference=base=='lightgbm_reference'
    names=list(REF_CAL if reference else CAL_NAMES)
    names += [f'lag_{k}_Wh' for k in ((1,24,168) if reference else LAGS)]
    names += [f'past_mean_{k}_Wh' for k in ((24,168) if reference else WINDOWS)]
    return names

def features(s,name):
    reference=name=='lightgbm_reference'
    f=pd.DataFrame(calendar(s.index,reference),index=s.index)
    for k in ((1,24,168) if reference else LAGS):f[f'lag_{k}']=s.shift(k)
    for k in ((24,168) if reference else WINDOWS):f[f'mean_{k}']=s.shift(1).rolling(k).mean()
    f.columns=feature_names(name)
    return f

def make_model(name):
    base=name
    if base=='ridge_lags':return make_pipeline(StandardScaler(),Ridge(alpha=1000))
    if base=='lightgbm_reference':return LGBMRegressor(objective='regression_l1',n_estimators=600,learning_rate=.05,num_leaves=31,subsample=.9,colsample_bytree=.9,n_jobs=4,random_state=42,verbosity=-1)
    if base=='lightgbm_lags':return LGBMRegressor(objective='regression_l1',n_estimators=300,learning_rate=.03,num_leaves=15,min_child_samples=48,reg_lambda=10,n_jobs=4,random_state=42,verbosity=-1)
    if base=='xgboost_lags':return XGBRegressor(objective='reg:squarederror',n_estimators=300,max_depth=3,min_child_weight=48,reg_lambda=20,learning_rate=.03,n_jobs=4,random_state=42)
    if base=='hist_lags':return HistGradientBoostingRegressor(loss='absolute_error',max_iter=120,max_leaf_nodes=7,min_samples_leaf=80,l2_regularization=10,early_stopping=False,random_state=42)
    if base=='extra_trees_lags':return ExtraTreesRegressor(n_estimators=100,max_depth=6,min_samples_leaf=40,n_jobs=1,random_state=42)
    raise ValueError(name)

def train(s,name):
    if name=='train_mean':return {'constant_Wh':float(s.mean()),'fit_rows':len(s)}
    if name=='train_median':return {'constant_Wh':float(s.median()),'fit_rows':len(s)}
    f=features(s,name);valid=f.notna().all(axis=1);target=s.loc[valid].to_numpy()
    center=0.0 if name=='lightgbm_reference' else float(target.mean())
    m=make_model(name);m.fit(f.loc[valid].to_numpy(),target-center)
    return {'estimator':m,'center_Wh':center,'fit_rows':int(valid.sum())}

def forecast(s,dates,name,bundle):
    assert dates[0]==s.index[-1]+pd.Timedelta(hours=1)
    if 'constant_Wh' in bundle:return np.full(len(dates),bundle['constant_Wh'])
    base=name;reference=base=='lightgbm_reference'
    lags=(1,24,168) if reference else LAGS;windows=(24,168) if reference else WINDOWS
    a=list(s.to_numpy(dtype=float));values=[];m=bundle['estimator'];cal=calendar(dates,reference)
    for row in cal:
        extra=[a[-k] for k in lags]+[np.mean(a[-k:]) for k in windows]
        x=np.r_[row,extra].reshape(1,-1)
        if base.startswith('lightgbm'):raw=m.booster_.predict(x,num_threads=1)[0]
        else:raw=m.predict(x)[0]
        value=max(0,float(raw)+bundle['center_Wh']);a.append(value);values.append(value)
    return np.array(values,dtype=float)

def main(rebuild=False):
    for d in [OUT,OUT/'models',OUT/'plots']:d.mkdir(exist_ok=True)
    s,t,h,q=prepare_data(rebuild);y=t.hourly_pow
    folds=[];fold_table=[]
    for i,back in enumerate([60,30,0]):
        end=s.index.max()-pd.Timedelta(days=back);start=end-pd.Timedelta(hours=719)
        a=s.loc[s.index<start];b=s.loc[start:end];folds.append((a,b))
        fold_table.append({'fold':i+1,'train_start':str(a.index.min()),'train_end':str(a.index.max()),'valid_start':str(b.index.min()),'valid_end':str(b.index.max()),'valid_hours':len(b)})
    fold_table=pd.DataFrame(fold_table);save_csv(fold_table,'cv_folds.csv')
    dates=pd.date_range(s.index[-1]+pd.Timedelta(hours=1),y.index.max().normalize()+pd.Timedelta(hours=23),freq='h')
    pred=pd.DataFrame({'actual':y});future=pd.DataFrame(index=dates);scores=[];rows=[];bundles={}
    altered=s.copy();altered.iloc[1000:]+=12345
    for name in NAMES:
        if not name.startswith('train_'):np.testing.assert_allclose(features(s,name).iloc[1000],features(altered,name).iloc[1000],atol=1e-9)
        vals=[]
        for i,(a,b) in enumerate(folds):
            bundle=train(a,name);p=forecast(a,b.index,name,bundle);v=float(mean_absolute_error(b,p));vals.append(v)
            scores.append({'model':name,'fold':i+1,'mae_Wh':v})
        bundle=train(s,name);f=forecast(s,dates,name,bundle);future[name]=f;pred[name]=future[name].reindex(y.index)
        assert pred[name].notna().all() and np.isfinite(pred[name]).all()
        rows.append({'model':name,'cv_mae':float(np.mean(vals)),'mae':float(mean_absolute_error(y,pred[name]))})
        bundles[name]=bundle
        joblib.dump({'name':name,'bundle':bundle,'feature_names':feature_names(name),'target_unit':'Wh','training_end':str(s.index.max())},OUT/'models'/(name+'.joblib'))
        print(f'{name}: CV={np.mean(vals):.4f} Wh; May={rows[-1]["mae"]:.4f} Wh',flush=True)
    met=pd.DataFrame(rows);selected=str(met.loc[met.cv_mae.idxmin(),'model']);best_ml=str(met.loc[~met.model.str.startswith('train_')].sort_values('cv_mae').iloc[0].model)
    met=met.sort_values('mae').reset_index(drop=True)
    means=pd.DataFrame([{'model':name,'actual_mean_Wh':float(y.mean()),'pred_mean_Wh':float(pred[name].mean())} for name in met.model])
    save_csv(met,'model_comparison.csv');save_csv(means,'mean_comparison.csv');save_csv(pd.DataFrame(scores),'cv_scores.csv');save_csv(pred,'predictions.csv',index=True)
    f=features(s,best_ml);save_csv(f.join(s.rename('target_energy_Wh')),'training_features.csv',index=True)
    fd=pd.DataFrame({'hour':dates,'pred_hourly_pow':future[selected].to_numpy()});fd['pred_energy_Wh']=fd.pred_hourly_pow;fd['pred_bill_krw']=fd.pred_hourly_pow/1000*180;fd['pred_carbon_kg']=fd.pred_hourly_pow/1000*.424
    fd['horizon_day']=np.arange(len(fd))<24;fd['horizon_week']=np.arange(len(fd))<168;fd['horizon_month']=fd.hour.dt.month==5;save_csv(fd,'future_forecasts.csv')
    row=met.set_index('model').loc[selected]
    summary={'best_model':selected,'cv_best_model':selected,'best_ml_model':best_ml,'selection_metric':'Lowest mean MAE across identical three historical 720-hour folds; May is a repeatedly inspected comparison set.','feature_set':'calendar + lag + past means','train_range':{'start':str(s.index.min()),'end':str(s.index.max()),'valid_hour_rows':len(s)},'may_holdout_range':{'start':str(y.index.min()),'end':str(y.index.max()),'rows':len(y)},'may_holdout_metrics':{'MAE_hourly_pow':float(row.mae),'MAE_bill_krw':float(row.mae/1000*180),'MAE_carbon_kg':float(row.mae/1000*.424)},'data_quality':{'equipment_count':13,'low_coverage_hours':q['partial_or_missing_hours']},'unit_assumptions':{'activePower':'W per session-14 pages 9/25','hourly_pow':'Wh inferred for truth','electricity_rate_won_per_kwh':180,'carbon_factor_kg_per_kwh':.424,'truth_may_bill_to_calculated_bill_median_ratio':float((t.may_bill/(y/1000*180)).median()),'truth_may_carbon_to_calculated_carbon_median_ratio':float((t.may_carbon/(y/1000*.424)).median())},'model_comparison':met.to_dict(orient='records')}
    dump_json(summary,'summary.json')
    pdf=create_report(met,pred,means,fold_table,q,selected,best_ml,LABELS)
    saved=pd.read_csv(OUT/'predictions.csv',index_col=0)
    for r in met.itertuples():np.testing.assert_allclose(np.abs(saved[r.model]-saved.actual).mean(),r.mae,atol=1e-7)
    check=joblib.load(OUT/'models'/(best_ml+'.joblib'))
    np.testing.assert_allclose(forecast(s,dates[:24],best_ml,check['bundle']),future[best_ml].iloc[:24],atol=1e-9)
    assert not any('std' in x for name in NAMES for x in feature_names(name))
    print('SELECTED:',selected,'BEST_ML:',best_ml,flush=True)
    print(met.to_string(index=False),flush=True)
    print('PDF:',pdf,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--rebuild-data',action='store_true');args=parser.parse_args();main(args.rebuild_data)
