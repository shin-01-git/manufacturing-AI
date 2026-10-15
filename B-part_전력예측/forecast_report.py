# -*- coding: utf-8 -*-
"""Build the concise Korean PDF report and one prediction page per model."""
from pathlib import Path
from xml.sax.saxutils import escape
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak,KeepTogether

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'audit_outputs'
PLOTS=OUT/'plots'
FONT=Path('C:/Windows/Fonts')

def create_report(met,pred,means,folds,quality,selected,best_ml,labels):
    PLOTS.mkdir(exist_ok=True)
    plt.rcParams['font.family']='Malgun Gothic';plt.rcParams['axes.unicode_minus']=False
    pdfmetrics.registerFont(TTFont('Malgun',str(FONT/'malgun.ttf')))
    pdfmetrics.registerFont(TTFont('MalgunBold',str(FONT/'malgunbd.ttf')))
    pdfmetrics.registerFontFamily('Malgun',normal='Malgun',bold='MalgunBold',italic='Malgun',boldItalic='MalgunBold')
    title=ParagraphStyle('Title',fontName='MalgunBold',fontSize=23,leading=32,textColor=colors.HexColor('#16324f'),spaceAfter=16,wordWrap='CJK')
    heading=ParagraphStyle('Heading',fontName='MalgunBold',fontSize=14,leading=22,spaceBefore=14,spaceAfter=9,keepWithNext=True,wordWrap='CJK')
    body=ParagraphStyle('Body',fontName='Malgun',fontSize=10,leading=17,spaceAfter=8,wordWrap='CJK')
    small=ParagraphStyle('Small',fontName='Malgun',fontSize=8.5,leading=13,spaceAfter=6,wordWrap='CJK')
    cell=ParagraphStyle('Cell',fontName='Malgun',fontSize=9,leading=14,wordWrap='CJK')
    cell_head=ParagraphStyle('CellHead',parent=cell,fontName='MalgunBold',textColor=colors.HexColor('#16324f'))
    def p(text,style=body):return Paragraph(text,style)
    def table(headers,rows,widths):
        data=[[p(escape(str(x)),cell_head) for x in headers]]+[[p(escape(str(x)),cell) for x in row] for row in rows]
        result=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
        result.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf1f7')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#b7c6d6')),('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#d9e0e8')),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return result
    def plot_file(fig,name):
        fig.tight_layout();path=PLOTS/name;fig.savefig(path,dpi=160,bbox_inches='tight');plt.close(fig);return path
    def image(path,width=178*mm):
        from PIL import Image as PILImage
        with PILImage.open(path) as img:w,h=img.size
        return Image(str(path),width=width,height=width*h/w)
    order=met.sort_values('cv_mae').model.tolist();indexed=met.set_index('model')
    fig,ax=plt.subplots(figsize=(11.6,5.2));positions=np.arange(len(order));w=.36
    ax.barh(positions-w/2,[indexed.loc[n,'cv_mae'] for n in order],height=w,label='과거 3회 검증 평균',color='#346889')
    ax.barh(positions+w/2,[indexed.loc[n,'mae'] for n in order],height=w,label='5월 비교',color='#d99345')
    ax.set_yticks(positions,[labels[n] for n in order]);ax.invert_yaxis();ax.set_xlabel('MAE (Wh) · 낮을수록 좋음');ax.legend(loc='lower right');ax.grid(axis='x',alpha=.2)
    comparison=plot_file(fig,'model_comparison.png')
    mean_index=means.set_index('model');actual_mean=float(pred.actual.mean())
    fig,ax=plt.subplots(figsize=(11.6,4.8));ax.barh(positions,[mean_index.loc[n,'pred_mean_Wh']-actual_mean for n in order],color='#346889')
    ax.set_yticks(positions,[labels[n] for n in order]);ax.invert_yaxis();ax.axvline(0,color='black',lw=.8);ax.set_xlabel('예측 평균 - 실제 평균 (Wh)');ax.set_title(f'실제 평균: {actual_mean:,.2f} Wh');ax.grid(axis='x',alpha=.2)
    average=plot_file(fig,'mean_comparison.png')
    model_images={}
    all_values=pred.to_numpy();low=float(all_values.min()-20);high=float(all_values.max()+20)
    zoom_values=pred.iloc[:96].to_numpy();zoom_low=float(zoom_values.min()-20);zoom_high=float(zoom_values.max()+20)
    for name in order:
        fig,axes=plt.subplots(2,1,figsize=(11.6,7.7))
        for ax,data,view,limits in [(axes[0],pred,'5월 평가 전체 505시간',(low,high)),(axes[1],pred.iloc[:96],'처음 96시간 확대',(zoom_low,zoom_high))]:
            ax.plot(data.index,data.actual,color='#252525',lw=1,label='실제')
            ax.plot(data.index,data[name],color='#267bb5',lw=1.2,label='예측')
            ax.set_title(view);ax.set_ylabel('시간별 전력량 (Wh)');ax.set_ylim(*limits);ax.ticklabel_format(axis='y',style='plain',useOffset=False);ax.grid(alpha=.2);ax.legend(loc='upper right');ax.tick_params(axis='x',labelrotation=20)
        model_images[name]=plot_file(fig,'prediction_'+name+'.png')
    width=A4[0]-36*mm
    story=[p('설비 전력 예측 분석 보고서',title),p('lag·이동 평균을 사용한 모델 비교',heading),p('추가 표준편차 특징을 제거하고, 동일한 검증 구간에서 8개 후보를 비교했습니다. 성능은 MAE로 정리하고 모델별 실제·예측 그래프를 제공합니다.')]
    story += [p('1. 분석 데이터와 단위',heading),p('학습 데이터는 2024년 12월 1일~2025년 4월 29일의 완전한 3,600시간입니다. 원본은 13개 설비에서 5초마다 측정한 33,696,013행입니다. 정답은 2025년 5월 10일 0시~5월 31일 0시의 505시간입니다.'),p('session-14.pdf 9·25쪽의 규칙에 따라 activePower는 W, 시간별 전력량은 Wh로 해석합니다. truth의 단위는 문서 기준과 원본 집계 규모를 근거로 Wh로 판단했습니다. 설비 13대의 시간별 합계가 예측 대상입니다.')]
    story += [p('2. 계산 방법과 데이터 정리',heading),p('<b>5초 구간 전력량(Wh) = activePower(W) × 5 ÷ 3,600</b>'),p('<b>설비별 한 시간 사용량 = 해당 시간의 720개 구간 전력량 합계</b>'),p('<b>전체 한 시간 사용량 = 13개 설비 사용량 합계</b>'),p('예를 들어 2,961.61 W가 5초를 대표하면 사용량은 약 4.113 Wh입니다. 한 시간 내내 3,000 W를 사용하면 3,000 Wh = 3 kWh입니다. 00:00:00~00:59:55까지의 측정값으로 00시~01시 사용량을 계산합니다.'),p('설비별 정확히 720개 표본이 있는 시간만 학습합니다. 마지막 4월 30일 0시에는 설비당 한 개 표본만 있어 학습에서 제외했습니다. 결측 구간을 보간하거나 부분 시간 사용량을 한 시간으로 확대하지 않았습니다.')]
    story += [PageBreak(),p('3. 학습 모델',heading)]
    configs=[['학습 평균·중앙값','학습 구간의 평균 또는 중앙값을 계속 예측하는 비교 기준'],['Ridge','표준화 + Ridge, alpha=1,000'],['LightGBM 보수 설정','L1 손실, 300회, 학습률 0.03, leaf 15, 최소 표본 48, L2=10'],['LightGBM 기존 설정','L1 손실, 600회, 학습률 0.05, leaf 31, 열 표본 비율 0.9'],['XGBoost','제곱오차 손실, 300회, 깊이 3, 학습률 0.03, L2=20'],['HistGradientBoosting','L1 손실, 120회, leaf 7, 최소 표본 80, L2=10'],['ExtraTrees','트리 100개, 깊이 6, 최소 leaf 표본 40']]
    story += [table(['모델','설정'],configs,[52*mm,width-52*mm]),p('4. 적용 기법',heading),p('<b>달력 정보:</b> 시간, 요일, 주말 여부, 시간·요일의 sin/cos 주기 변수를 사용합니다. 기존 설정 LightGBM은 월·일도 유지합니다.'),p('<b>과거 사용량(lag):</b> 일반 모델은 1·2·3·24·48·168시간 전 값을 사용합니다. 기존 설정 LightGBM은 1·24·168시간 전 값을 사용합니다.'),p('<b>이동 평균:</b> 일반 모델은 직전 6·24·168시간 평균, 기존 설정 LightGBM은 직전 24·168시간 평균을 사용합니다. 모두 현재 시점은 제외합니다.'),p('<b>재귀 예측:</b> 미래의 실제 사용량을 미리 넣지 않습니다. 예측한 값을 이력에 추가해 다음 시점의 lag·이동 평균을 계산합니다.'),p('<b>학습 기준:</b> 표준화와 타깃 중심화는 해당 학습 구간에서만 계산합니다. 학습 모델의 유효 행은 각각 3,432개이고, 평균·중앙값 기준은 전체 3,600시간을 사용합니다. seasonal naive와 추가 표준편차는 후보에서 제외했습니다.')]
    story += [PageBreak(),p('5. 검증 구성',heading)]
    fold_rows=[]
    for row in folds.itertuples():
        fold_rows.append([str(row.fold),pd.Timestamp(row.train_end).strftime('%Y-%m-%d %H시'),pd.Timestamp(row.valid_start).strftime('%m-%d %H시')+' ~ '+pd.Timestamp(row.valid_end).strftime('%m-%d %H시')])
    story += [table(['회차','학습 종료','2025년 검증 구간 · 각 720시간'],fold_rows,[18*mm,61*mm,width-79*mm]),p('과거 3회 검증 평균은 모델 선정에 사용하고, 5월은 그 이후 구간의 성능 비교에 사용합니다. 5월은 이전 분석부터 반복 확인한 자료여서 미공개 최종 테스트로 해석하지 않습니다. 두 점수는 한 그래프에 묶어 비교합니다.',small),p('6. 모델 성능 비교',heading)]
    performance=[[labels[n],f'{indexed.loc[n,"cv_mae"]:.2f}',f'{indexed.loc[n,"mae"]:.2f}'] for n in order]
    story += [table(['모델','과거 검증 MAE (Wh)','5월 MAE (Wh)'],performance,[width*.44,width*.28,width*.28]),Spacer(1,5*mm),image(comparison),p('MAE는 실제와 예측의 차이를 절댓값으로 평균한 값입니다. 낮을수록 좋습니다.',small)]
    story += [PageBreak(),p('7. 실제·예측 평균 비교',heading),p(f'동일한 5월 505시간을 비교했습니다. 실제 평균은 <b>{actual_mean:,.2f} Wh</b>입니다. 평균이 가까운지와 시간별 변동을 맞추는지는 구분해서 봐야 합니다.')]
    mean_rows=[[labels[n],f'{actual_mean:,.2f}',f'{mean_index.loc[n,"pred_mean_Wh"]:,.2f}'] for n in order]
    story += [table(['모델','실제 평균 (Wh)','예측 평균 (Wh)'],mean_rows,[width*.44,width*.28,width*.28]),Spacer(1,5*mm),image(average)]
    for i,name in enumerate(order):
        story += [PageBreak(),p('8. 모델별 실제·예측 그래프',heading),p(f'8-{i+1}. {labels[name]}',title),p(f'5월 MAE: <b>{indexed.loc[name,"mae"]:.2f} Wh</b> · 검정: 실제 / 파랑: 예측'),image(model_images[name]),p('위쪽은 평가 전체 구간, 아래쪽은 처음 96시간입니다. 모델 사이의 비교를 위해 모든 페이지에 같은 세로축 범위를 적용했습니다.',small)]
    chosen=indexed.loc[selected];ml=indexed.loc[best_ml];may_name=str(met.loc[met.mae.idxmin(),'model'])
    story += [PageBreak(),p('9. 선정 이유와 결과 해석',heading),p(f'<b>전체 비교 기준: {labels[selected]}</b><br/>과거 3회 평균 MAE가 {chosen.cv_mae:.2f} Wh로 가장 낮아 선정했습니다. 학습 모델이 단순한 기준보다 개선되는지 확인하는 용도입니다.'),p(f'<b>학습 모델 중 우선 후보: {labels[best_ml]}</b><br/>학습 모델 가운데 과거 검증 MAE가 {ml.cv_mae:.2f} Wh로 가장 낮았습니다. lag·이동 평균은 유지하고 추가 표준편차는 사용하지 않습니다.'),p(f'<b>5월 비교 성능: {labels[may_name]}</b><br/>5월 MAE는 {indexed.loc[may_name,"mae"]:.2f} Wh로 가장 낮았습니다. 다만 5월 점수를 보고 모델을 고르면 같은 평가 자료에 맞춘 선택이 되므로, 선정 기준은 과거 검증으로 유지합니다.'),p('추가 표준편차는 일부 설정에서만 소폭 개선됐고 전체 순위와 개선의 일관성을 확보하지 못해 제거했습니다. 현재 입력으로 평균 사용 수준은 비슷하게 예측하지만, 실제·예측 그래프에서 시간별 변동을 충분히 따라가지 못하는 경향이 남아 있습니다.'),p('생산량·가동 계획·설비 상태와 같은 부하 설명 정보와 별도 미래 구간을 확보해 개선 여부를 확인할 필요가 있습니다.')]
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Malgun',8);canvas.setFillColor(colors.HexColor('#627487'));canvas.drawString(18*mm,12*mm,'설비 전력 예측 분석');canvas.drawRightString(A4[0]-18*mm,12*mm,str(doc.page));canvas.restoreState()
    path=OUT/'analysis_report.pdf'
    doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=16*mm,bottomMargin=20*mm,title='설비 전력 예측 분석 보고서',author='RTU Energy Forecast')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return path
