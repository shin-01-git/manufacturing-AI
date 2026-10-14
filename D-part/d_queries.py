# ============================================================
# D 파트 - 조회 함수 모음
#
# 역할: "데이터에서 원하는 값을 꺼내오는 기능"들의 모음.
# 나중에 AI(function calling)가 이 함수들을 골라 호출해서 질문에 답한다.
#
# [설계 원칙]
#  - 숫자 계산은 전부 '코드'(이 함수들)가 한다. AI는 숫자를 만들지 않는다.
#  - 데이터는 '밖에서 인자(df)로 받는다'. 이 파일이 직접 파일을 읽지 않는다.
#    -> A가 어떤 형태로 집계 데이터를 주든, df로 넘겨주기만 하면 작동한다.
#  - 데이터를 임의로 생성하지 않는다.
#  - 아직 A 데이터가 없으므로, 데이터가 필요한 함수는 df=None이면
#    "데이터 아직 없음"을 알리는 결과를 돌려준다 (오류로 멈추지 않음).
#
# [확정된 계산식] (ground_truth 분석으로 검증됨)
#    요금  = 전력량 × 180
#    탄소 = 전력량 × 0.424
# ============================================================

from d_roles import BILL_RATE, CARBON_RATE, TREE_ABSORB_KG, TREE_SOURCE


# ------------------------------------------------------------
# [그룹 1] 데이터가 필요 없는 '계산' 함수 — 지금 바로 완성/작동
# ------------------------------------------------------------
def calc_bill(energy_kwh):
    """전력량을 요금(원)으로 환산한다. 요금 = 전력량 × 180"""
    return energy_kwh * BILL_RATE


def calc_carbon(energy_kwh):
    """전력량을 탄소배출량으로 환산한다. 탄소 = 전력량 × 0.424"""
    return energy_kwh * CARBON_RATE


def calc_trees(carbon_kg):
    """탄소배출량(kg)을 '소나무 몇 그루가 1년간 흡수하는 양'으로 환산한다.
    기준: 산림청 — 소나무 1그루 연간 약 6.6kg CO2 흡수.
    예) 탄소 6.6kg = 나무 1그루, 13.2kg = 2그루."""
    if not TREE_ABSORB_KG:
        return 0
    return carbon_kg / TREE_ABSORB_KG


def summarize_energy(energy_kwh):
    """전력량 하나로 요금·탄소·나무그루수를 한 번에 계산해 딕셔너리로 돌려준다."""
    carbon = calc_carbon(energy_kwh)
    return {
        "energy": energy_kwh,
        "bill": calc_bill(energy_kwh),
        "carbon": carbon,
        "trees": calc_trees(carbon),          # 탄소 상쇄에 필요한 소나무 그루 수
        "tree_source": TREE_SOURCE,            # 환산 기준 출처(산림청)
    }


# ------------------------------------------------------------
# [집계본 로더] d_preprocess.py가 만든 가벼운 집계 파일을 불러온다.
#   컬럼: hour, module(equipment), kwh_sum, ap_mean, ap_min, ap_max,
#         volt_mean, curr_mean, pf_mean, pf_min, n, bill, carbon
# ------------------------------------------------------------
import os
import pandas as pd

AGG_PATH = r"C:\Users\82104\Desktop\DONG-EUI UNIVERSITY\d_aggregated.csv"


def load_aggregated(path=AGG_PATH):
    """집계본 CSV를 읽어 DataFrame으로 돌려준다. 없으면 None."""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"hour": str, "module(equipment)": str})
    return df


def _to_hour_key(value, is_end=False):
    """'2025-02-08', date 객체, '20250208', '2025020816' 모두 YYYYMMDDhh로 맞춘다."""
    if value is None:
        return None
    s = str(value).replace("-", "").replace(" ", "").replace(":", "")[:10]
    if len(s) == 8:
        s += "23" if is_end else "00"
    return s


def _filter_period(df, start=None, end=None):
    sub = df
    hours = sub["hour"].astype(str)
    s = _to_hour_key(start)
    e = _to_hour_key(end, is_end=True)
    if s:
        sub = sub[hours >= s]
        hours = sub["hour"].astype(str)
    if e:
        sub = sub[hours <= e]
    return sub


def hour_to_text(hour):
    """hour 코드(YYYYMMDDhh)를 사람이 읽는 'YYYY-MM-DD HH시'로 바꾼다.
    예: '2025020816' -> '2025-02-08 16시'"""
    h = str(hour)
    if len(h) != 10:
        return h
    return f"{h[0:4]}-{h[4:6]}-{h[6:8]} {h[8:10]}시"


def hour_to_date(hour):
    """hour 코드(YYYYMMDDhh) -> 'YYYY-MM-DD' (날짜만)."""
    h = str(hour)
    return f"{h[0:4]}-{h[4:6]}-{h[6:8]}" if len(h) == 10 else h


def date_range(df):
    """집계본에 들어있는 날짜의 (최소, 최대)를 'YYYY-MM-DD'로 돌려준다."""
    hours = df["hour"].astype(str)
    return hour_to_date(hours.min()), hour_to_date(hours.max())


def filter_by_date(df, date_from=None, date_to=None):
    """'YYYY-MM-DD' 문자열로 날짜 범위를 거른다. (hour 앞 8자리로 비교)"""
    sub = df
    d = sub["hour"].astype(str).str[:8]   # YYYYMMDD
    if date_from:
        sub = sub[d >= str(date_from).replace("-", "")]
        d = sub["hour"].astype(str).str[:8]
    if date_to:
        sub = sub[d <= str(date_to).replace("-", "")]
    return sub


def detect_power_anomalies(df, start=None, end=None, sigma=3.0, top=50):
    """전력 기준 이상치 탐지 (3σ 규칙).
    설비별로 '평소 전력 평균 ± sigma*표준편차'를 벗어난 시간대를 찾는다.
    '고장'으로 단정하지 않고, 관찰된 사실(얼마나 벗어났는지)만 돌려준다.

    반환 items: [{설비, 시각(읽기쉬운), hour, 전력, 평소평균, 벗어난배수, 방향}, ...]
    """
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    if len(sub) == 0:
        return {"status": "empty", "message": "해당 기간 데이터가 없습니다."}

    results = []
    # 설비별로 평소 전력 분포를 구해 기준선을 만든다
    for equip, g in sub.groupby("module(equipment)"):
        mean = g["ap_mean"].mean()
        std = g["ap_mean"].std()
        if std == 0 or pd.isna(std):
            continue
        upper = mean + sigma * std
        lower = mean - sigma * std
        # 평소 범위를 벗어난 시간대
        out = g[(g["ap_mean"] > upper) | (g["ap_mean"] < lower)]
        for _, r in out.iterrows():
            ratio = r["ap_mean"] / mean if mean else 0
            results.append({
                "equipment": equip,
                "time": hour_to_text(r["hour"]),
                "hour": r["hour"],
                "power": round(float(r["ap_mean"]), 1),
                "normal_mean": round(float(mean), 1),
                "ratio": round(float(ratio), 2),          # 평소 대비 배수
                "direction": "높음" if r["ap_mean"] > upper else "낮음",
            })

    # 평소에서 많이 벗어난 순으로 정렬 (배수 기준)
    results.sort(key=lambda x: abs(x["ratio"] - 1), reverse=True)
    return {
        "status": "ok",
        "period": (start, end),
        "sigma": sigma,
        "count": len(results),
        "items": results[:top],
    }


# ------------------------------------------------------------
# [그룹 2] 데이터가 필요한 '조회' 함수 — 집계본(df)으로 실제 작동
#   공통 규칙: 첫 인자 df 는 집계본 DataFrame.
#            df 가 None 이면 "데이터 없음" 메시지를 돌려준다.
# ------------------------------------------------------------
_NO_DATA = {"status": "no_data",
            "message": "아직 집계 데이터(df)가 연결되지 않았습니다. A의 데이터 수령 후 사용 가능합니다."}


def resolve_equipment(df, name):
    """사용자가 말한 설비 표현을 실제 설비명으로 바꿔준다.
    예) '3호기' -> '13(3호기)',  '호이스트' -> '16(호이스트)',  '13' -> '13(3호기)'
    못 찾으면 (None, 후보목록) 을 돌려준다.

    반환: (정확한_설비명 또는 None, 후보_리스트)
    """
    if df is None or not name:
        return (None, [])
    all_names = df["module(equipment)"].astype(str).unique().tolist()

    s = str(name).strip()
    # 1) 완전 일치
    if s in all_names:
        return (s, [s])

    # 2) 괄호 안 별명 일치 (예: '3호기' == 13(3호기) 의 괄호 안)
    #    괄호 안 전체 또는 그 일부가 사용자 입력과 맞는지 본다.
    import re as _re
    s_norm = s.replace(" ", "")
    cand = []
    for n in all_names:
        m = _re.search(r"\((.+)\)", n)      # 괄호 안 텍스트
        inside = m.group(1) if m else ""
        head = n.split("(")[0]              # 앞 번호 (예: '13')
        inside_norm = inside.replace(" ", "")
        if s_norm == inside_norm or s_norm == head:
            return (n, [n])
        # 부분 포함 후보 (예: '분쇄기' -> 3(분쇄기(2)), 4(분쇄기(1)))
        if s_norm and (s_norm in inside_norm or s_norm in n.replace(" ", "")):
            cand.append(n)

    if len(cand) == 1:
        return (cand[0], cand)
    return (None, cand)   # 0개(못 찾음) 또는 2개 이상(모호)


def _equip_not_found(name, candidates):
    """설비를 못 찾았을 때의 표준 응답."""
    msg = f"'{name}' 설비를 데이터에서 찾지 못했습니다."
    if candidates:
        msg += " 혹시 이 중 하나인가요? " + ", ".join(candidates)
    return {"status": "not_found", "equipment": name,
            "candidates": candidates, "message": msg}


def get_equipment_power(df, equipment, start=None, end=None):
    """특정 설비의 전력(평균/최소/최대)과 역률을 기간 범위에서 조회한다."""
    if df is None:
        return _NO_DATA
    resolved, candidates = resolve_equipment(df, equipment)
    if resolved is None:
        return _equip_not_found(equipment, candidates)
    equipment = resolved
    sub = _filter_period(df, start, end)
    sub = sub[sub["module(equipment)"] == equipment]
    if len(sub) == 0:
        return {"status": "empty", "equipment": equipment, "period": (start, end),
                "message": "해당 설비/기간 데이터가 없습니다."}
    return {
        "status": "ok",
        "equipment": equipment,
        "period": (start, end),
        "ap_mean": round(float(sub["ap_mean"].mean()), 2),
        "ap_min": round(float(sub["ap_min"].min()), 2),
        "ap_max": round(float(sub["ap_max"].max()), 2),
        "pf_mean": round(float(sub["pf_mean"].mean()), 2),
        "pf_min": round(float(sub["pf_min"].min()), 2),
        # 전압 3상 (평균/최소/최대) — 순간 피크 확인용
        "vR_max": round(float(sub["vR_max"].max()), 2),
        "vS_max": round(float(sub["vS_max"].max()), 2),
        "vT_max": round(float(sub["vT_max"].max()), 2),
        "vR_min": round(float(sub["vR_min"].min()), 2),
        # 전류 3상 최대 — 순간 피크 확인용
        "cR_max": round(float(sub["cR_max"].max()), 2),
        "cS_max": round(float(sub["cS_max"].max()), 2),
        "cT_max": round(float(sub["cT_max"].max()), 2),
        # 가동률 (0~1)
        "op_ratio": round(float(sub["op_ratio"].mean()), 3),
        "hours": int(len(sub)),
    }


def get_equipment_energy(df, equipment, start=None, end=None):
    """특정 설비의 전력량(kWh 합계)과 요금·탄소를 조회한다."""
    if df is None:
        return _NO_DATA
    resolved, candidates = resolve_equipment(df, equipment)
    if resolved is None:
        return _equip_not_found(equipment, candidates)
    equipment = resolved
    sub = _filter_period(df, start, end)
    sub = sub[sub["module(equipment)"] == equipment]
    if len(sub) == 0:
        return {"status": "empty", "equipment": equipment, "period": (start, end),
                "message": "해당 설비/기간 데이터가 없습니다."}
    kwh = float(sub["kwh_sum"].sum())
    out = summarize_energy(kwh)
    out.update({"status": "ok", "equipment": equipment, "period": (start, end)})
    return out


def get_total_energy(df, start=None, end=None):
    """전체 설비의 전력량(kWh 합계)과 요금·탄소를 조회한다. (에너지 담당용)"""
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    if len(sub) == 0:
        return {"status": "empty", "period": (start, end),
                "message": "해당 기간 데이터가 없습니다."}
    kwh = float(sub["kwh_sum"].sum())
    out = summarize_energy(kwh)
    out.update({"status": "ok", "period": (start, end),
                "equipment_count": int(sub["module(equipment)"].nunique())})
    return out


def get_anomalies(df, start=None, end=None, pf_threshold=70.0):
    """기간 내 '역률이 비정상적으로 낮았던' 구간을 조회한다. (설비 담당용)
    참고: 본격 이상탐지는 C 담당. 여기서는 pf_min이 임계값 미만인 구간을
    '관찰된 사실'로만 돌려준다(단정하지 않음)."""
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    flagged = sub[sub["pf_min"] < pf_threshold].sort_values("pf_min")
    items = [
        {"time": hour_to_text(r["hour"]), "hour": r["hour"],
         "equipment": r["module(equipment)"],
         "pf_min": round(float(r["pf_min"]), 2), "ap_max": round(float(r["ap_max"]), 2)}
        for _, r in flagged.head(50).iterrows()
    ]
    return {"status": "ok", "period": (start, end),
            "pf_threshold": pf_threshold, "count": len(flagged), "items": items}


def rank_carbon_by_equipment(df, start=None, end=None, top=5):
    """설비별 탄소 배출량 순위를 조회한다. (에너지 담당, 감축 대상 선정용)"""
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    if len(sub) == 0:
        return {"status": "empty", "period": (start, end),
                "message": "해당 기간 데이터가 없습니다."}
    g = sub.groupby("module(equipment)")["kwh_sum"].sum()
    ranked = g.sort_values(ascending=False).head(top)
    items = [
        {"equipment": name, "kwh": round(float(kwh), 2),
         "carbon": round(calc_carbon(float(kwh)), 2),
         "trees": round(calc_trees(calc_carbon(float(kwh))), 1),
         "bill": round(calc_bill(float(kwh)), 2)}
        for name, kwh in ranked.items()
    ]
    return {"status": "ok", "period": (start, end), "top": top,
            "tree_source": TREE_SOURCE, "items": items}


# ------------------------------------------------------------
# [역할별 요약 문장] 조회 결과를 엮어 '핵심 한 요약'을 만든다.
#   화면 상단 요약 박스 + AI 입력용으로 쓴다. (숫자는 코드가 계산)
# ------------------------------------------------------------
def get_data_overview(df):
    """데이터 개요: 실제 기간, 설비 수, 행 수, 이상치/역률저하 건수.
    AI가 '기간이 언제인지' 등을 지어내지 않도록 사실을 제공한다."""
    if df is None:
        return _NO_DATA
    dmin, dmax = date_range(df)
    an = detect_power_anomalies(df, sigma=3.0, top=100000)
    pf = get_anomalies(df)
    return {
        "status": "ok",
        "data_start": dmin,          # 데이터 시작일 (YYYY-MM-DD)
        "data_end": dmax,            # 데이터 끝일
        "equipment_count": int(df["module(equipment)"].nunique()),
        "row_count": int(len(df)),
        "power_anomaly_count": an.get("count", 0),   # 전력 이상치 건수
        "pf_low_count": pf.get("count", 0),          # 역률 저하 건수
        "note": "이 값들은 실제 집계 데이터에서 계산된 사실입니다.",
    }


def summarize_for_role(df, role_id, start=None, end=None):
    """역할에 맞는 요약 문장(dict)을 돌려준다.
    반환: {title, lines[], period}"""
    if df is None:
        return {"title": "요약", "lines": ["집계 데이터가 없습니다."]}

    if role_id == "equipment_manager":
        an = detect_power_anomalies(df, start, end, sigma=3.0, top=100)
        pf = get_anomalies(df, start, end)
        lines = []
        if an.get("status") == "ok":
            lines.append(f"전력 이상 후보: {an['count']:,}건")
            if an["items"]:
                t = an["items"][0]
                lines.append(
                    f"가장 주의할 설비: {t['equipment']} "
                    f"({t['time']}, 평소 대비 {t['ratio']}배 {t['direction']})"
                )
        if pf.get("status") == "ok":
            lines.append(f"역률 저하(70 미만) 구간: {pf['count']:,}건 — 노후화 점검 권장")
        if not lines:
            lines = ["선택 기간에 특이 이상치가 없습니다."]
        return {"title": "⚙️ 설비 요약", "lines": lines, "period": (start, end)}

    elif role_id == "energy_manager":
        tot = get_total_energy(df, start, end)
        rank = rank_carbon_by_equipment(df, start, end, top=1)
        lines = []
        if tot.get("status") == "ok":
            lines.append(f"총 전력량 {tot['energy']:,.0f} kWh")
            lines.append(f"요금 약 {tot['bill']:,.0f} 원 / 탄소 약 {tot['carbon']:,.0f} kgCO₂")
        if rank.get("status") == "ok" and rank["items"]:
            top1 = rank["items"][0]
            lines.append(f"탄소 최다 배출 설비: {top1['equipment']} "
                         f"(탄소 {top1['carbon']:,.0f} kgCO₂)")
        if not lines:
            lines = ["선택 기간 데이터가 없습니다."]
        return {"title": "🌱 에너지 요약", "lines": lines, "period": (start, end)}

    return {"title": "요약", "lines": ["알 수 없는 역할입니다."]}


# ------------------------------------------------------------
# [설비 상태 현황판] 설비 13개 각각을 정상/주의/이상으로 판정한다.
#   이상 = 전력 3σ 이상치 있음 (빨강)
#   주의 = 역률 70 미만 구간 있음 (노랑)
#   정상 = 둘 다 없음 (초록)
# ------------------------------------------------------------
def equipment_status_board(df, start=None, end=None, pf_threshold=70.0):
    """설비별 상태 리스트를 돌려준다.
    반환 items: [{equipment, status(정상/주의/이상), anomaly_cnt, pf_low_cnt, ap_mean}]"""
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    if len(sub) == 0:
        return {"status": "empty", "items": []}

    # 설비별 이상치 수
    an = detect_power_anomalies(sub, start, end, sigma=3.0, top=100000)
    anom_by_eq = {}
    for it in an.get("items", []):
        anom_by_eq[it["equipment"]] = anom_by_eq.get(it["equipment"], 0) + 1

    items = []
    for eq, g in sub.groupby("module(equipment)"):
        a_cnt = anom_by_eq.get(eq, 0)
        pf_low = int((g["pf_min"] < pf_threshold).sum())
        if a_cnt > 0:
            status = "이상"
        elif pf_low > 0:
            status = "주의"
        else:
            status = "정상"
        items.append({
            "equipment": eq,
            "status": status,
            "anomaly_cnt": a_cnt,
            "pf_low_cnt": pf_low,
            "ap_mean": round(float(g["ap_mean"].mean()), 1),
        })
    # 이상 > 주의 > 정상 순으로 정렬 (문제 있는 게 위로)
    order = {"이상": 0, "주의": 1, "정상": 2}
    items.sort(key=lambda x: (order[x["status"]], -x["anomaly_cnt"]))
    return {"status": "ok", "items": items}


def monthly_compare(df, equipment=None):
    """월별(YYYYMM) 전력량/요금/탄소를 집계하고, 전월 대비 증감률을 계산한다.
    equipment=None이면 전체, 아니면 해당 설비만.
    반환 items: [{month, kwh, bill, carbon, kwh_diff_pct}]"""
    if df is None:
        return _NO_DATA
    sub = df if equipment is None else df[df["module(equipment)"] == equipment]
    if len(sub) == 0:
        return {"status": "empty", "items": []}

    sub = sub.copy()
    sub["month"] = sub["hour"].astype(str).str[:6]   # YYYYMM
    g = sub.groupby("month")["kwh_sum"].sum().sort_index()

    items = []
    prev = None
    for m, kwh in g.items():
        kwh = float(kwh)
        diff_pct = None if prev is None or prev == 0 else round((kwh - prev) / prev * 100, 1)
        items.append({
            "month": f"{m[:4]}-{m[4:6]}",   # YYYY-MM
            "kwh": round(kwh, 1),
            "bill": round(calc_bill(kwh), 0),
            "carbon": round(calc_carbon(kwh), 1),
            "kwh_diff_pct": diff_pct,       # 전월 대비 전력량 증감률(%)
        })
        prev = kwh
    return {"status": "ok", "items": items}


def analyze_efficiency(df, start=None, end=None,
                       pf_mean_threshold=85.0, pf_min_threshold=80.0,
                       low_op_threshold=0.5, top=10):
    """운영 효율성 분석 (역률 + 가동률 기반). '관찰된 사실'만 돌려준다.

    세 가지 관점으로 효율이 낮아 보이는 설비를 찾는다:
      1) 평균 역률 저하: pf_mean이 기준(pf_mean_threshold) 미만
         -> 전기를 지속적으로 비효율적으로 쓰는 중일 수 있음.
      2) 순간 역률 저하: pf_min이 기준(pf_min_threshold) 미만
         -> 평소엔 괜찮아도 특정 구간에 효율이 크게 떨어진 적 있음(노후·부하 변동 의심).
      3) 가동률 대비 과전력: 가동률(op_ratio)은 낮은데 평균 전력(ap_mean)은
         전체 평균 이상 -> 대기/공회전 전력 낭비 의심.

    [원칙] '고장'으로 단정하지 않는다. 사실과 '점검 권고'를 구분해 돌려준다.
    반환: {status, period, criteria, items:[{equipment, issue, detail, suggestion}]}
    """
    if df is None:
        return _NO_DATA
    sub = _filter_period(df, start, end)
    if len(sub) == 0:
        return {"status": "empty", "message": "해당 기간 데이터가 없습니다."}

    # 설비별 평균 지표 집계
    g = sub.groupby("module(equipment)").agg(
        pf_mean=("pf_mean", "mean"),
        pf_min=("pf_min", "min"),
        op_ratio=("op_ratio", "mean"),
        ap_mean=("ap_mean", "mean"),
        kwh=("kwh_sum", "sum"),
    )
    overall_ap = float(sub["ap_mean"].mean())   # 전체 평균 전력(비교 기준)

    items = []
    for eq, r in g.iterrows():
        pf = float(r["pf_mean"])
        pf_low = float(r["pf_min"])
        op = float(r["op_ratio"])
        ap = float(r["ap_mean"])

        # 1) 평균 역률 저하 관찰
        if pf < pf_mean_threshold:
            items.append({
                "equipment": eq,
                "issue": "평균 역률 저하(지속적 전기 효율 낮음)",
                "detail": f"평균 역률 {round(pf,1)} (기준 {pf_mean_threshold} 미만)",
                "suggestion": ("역률 보정 설비(콘덴서) 상태와 노후 여부를 점검하세요. "
                               "현재 데이터만으로 고장으로 단정할 수 없습니다."),
                "metric": round(pf, 1),
            })

        # 2) 순간 역률 저하 관찰 (평균은 괜찮아도 최저가 많이 낮은 경우)
        elif pf_low < pf_min_threshold:
            items.append({
                "equipment": eq,
                "issue": "순간 역률 저하(특정 구간 효율 급감)",
                "detail": f"평균 역률은 {round(pf,1)}로 양호하나, 역률 최저가 "
                          f"{round(pf_low,1)}까지 떨어진 구간이 있음 (기준 {pf_min_threshold} 미만)",
                "suggestion": ("역률이 낮아진 시간대의 부하 상태·가동 조건을 확인하고, "
                               "반복되면 역률 보정과 노후 점검을 검토하세요. 단정이 아닌 권고입니다."),
                "metric": round(pf_low, 1),
            })

        # 3) 가동률 대비 과전력 관찰 (가동률 낮은데 전력은 전체 평균 이상)
        if op < low_op_threshold and ap >= overall_ap:
            items.append({
                "equipment": eq,
                "issue": "가동률 대비 전력 과다(대기전력 낭비 의심)",
                "detail": f"가동률 {round(op*100,0):.0f}% 인데 평균 전력 {round(ap,1)}kW "
                          f"(전체 평균 {round(overall_ap,1)}kW 이상)",
                "suggestion": ("실제 가동 여부와 대기/공회전 상태 전력 사용을 확인하고, "
                               "미사용 시 전원 차단을 검토하세요. 단정이 아닌 점검 권고입니다."),
                "metric": round(op, 3),
            })

    # '더 심한 것'(수치 낮은 것)이 위로 오게 정렬
    items.sort(key=lambda x: x["metric"])
    return {
        "status": "ok",
        "period": (start, end),
        "criteria": {"pf_mean_threshold": pf_mean_threshold,
                     "pf_min_threshold": pf_min_threshold,
                     "low_op_threshold": low_op_threshold,
                     "overall_ap_mean": round(overall_ap, 1)},
        "count": len(items),
        "items": items[:top],
        "note": "관찰된 사실과 점검 권고만 제공합니다. 고장/손실을 확정하지 않습니다.",
    }


# ------------------------------------------------------------
# 혼자 테스트: 데이터 없이도 '계산 함수'는 지금 바로 작동하는지 확인
# ------------------------------------------------------------
if __name__ == "__main__":
    # 예시 전력량(검증용 숫자 하나로 계산만 확인) — 데이터 생성이 아니라 계산 테스트
    sample_energy = 39072.26  # ground_truth 첫 행의 hourly_pow 값(실제 데이터에서 온 값)
    result = summarize_energy(sample_energy)
    print("계산 함수 테스트:")
    print(f"  전력량 {result['energy']:,.2f}")
    print(f"  요금   {result['bill']:,.2f} 원   (= 전력량 x {BILL_RATE})")
    print(f"  탄소   {result['carbon']:,.2f}     (= 전력량 x {CARBON_RATE})")

    print("\n--- 실제 집계본으로 조회 테스트 ---")
    df = load_aggregated()
    if df is None:
        print("  집계본(d_aggregated.csv)이 없습니다.")
    else:
        print(f"  집계본 로드 OK: {len(df):,}행")

        print("\n[전체 전력량/요금/탄소] (12~4월 전체):")
        print(" ", get_total_energy(df))

        print("\n[특정 설비 전력] 1(PM-3):")
        print(" ", get_equipment_power(df, "1(PM-3)"))

        print("\n[특정 설비 전력량/요금/탄소] 1(PM-3):")
        print(" ", get_equipment_energy(df, "1(PM-3)"))

        print("\n[탄소 배출 상위 5개 설비]:")
        r = rank_carbon_by_equipment(df, top=5)
        for it in r["items"]:
            print(f"    {it['equipment']}: 탄소 {it['carbon']:,.0f} / 요금 {it['bill']:,.0f}원")

        print("\n[역률 낮았던 구간 (관찰된 사실, 상위 몇 개)]:")
        a = get_anomalies(df)
        print(f"    역률 70 미만 구간 수: {a['count']}")
        for it in a["items"][:5]:
            print(f"    {it['hour']} {it['equipment']}: 역률최저 {it['pf_min']}")
