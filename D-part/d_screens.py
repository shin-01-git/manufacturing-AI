# ============================================================
# D 파트 - 화면 (실데이터 연결 + 날짜/설비 필터 + 이상치 시각화)
#
# [중요] 이 파일은 "혼자 실행하는 앱"이 아니라, A의 공통 대시보드에
# 끼워 넣을 수 있는 '함수 묶음'이다.
#   - render_header()        : 서비스 제목/설명
#   - render_custom_info(df) : 화면 1 (맞춤 정보)
#   - render_ai_report(df)   : 화면 2 (AI 요약 보고서 / 상담)
#
# df: 집계본 DataFrame (d_aggregated.csv). None이면 내부에서 자동 로드한다.
# 데이터를 임의로 생성하지 않는다. 집계본(실데이터)만 사용한다.
# ============================================================

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from d_roles import list_roles, get_role, BILL_RATE, CARBON_RATE
import d_queries as q


# ------------------------------------------------------------
# 프로젝트 헤더 (팀 이름 / 서비스명 / 설명) — 과제 필수 요소
# ------------------------------------------------------------
TEAM_NAME = "문학적인 기계"
PROJECT_TITLE = "Factory Energy Copilot"
PROJECT_DESC = (
    "공장 13개 설비의 시간별 전력 데이터를 기반으로 사용량·비용·탄소배출량을 계산하고, "
    "설비별 기준선에서 벗어난 이상 사용을 찾아 근거 중심의 AI 점검 리포트를 제공하는 "
    "에너지 운영 지원 서비스입니다."
)
PROJECT_POINTS = [
    "어떤 설비가 에너지를 많이 사용하는지 보여준다.",
    "평소보다 얼마나 증가했는지 비교한다.",
    "해당 이상이 비용·탄소배출량에 어떤 영향을 주는지 계산한다.",
    "관리자가 무엇을 먼저 확인해야 하는지 AI가 정리한다.",
]
PROJECT_NOTE = (
    "※ 현재 데이터만으로 설비 고장이나 생산 손실을 확정하지 않습니다. "
    "에너지 사용 패턴의 이상 징후와 점검 우선순위를 제공합니다."
)


def render_header():
    """대시보드 상단: 서비스 제목만 간결하게 표시한다.
    (팀/담당·목표 설명 등은 기획 문서용이므로 화면에는 넣지 않는다.)"""
    st.title(f"⚡ {PROJECT_TITLE}")
    st.caption(PROJECT_NOTE)   # 데이터 해석상 주의 문구만 짧게 유지
    st.divider()


# ------------------------------------------------------------
# 데이터 준비
# ------------------------------------------------------------
@st.cache_data
def _load_df():
    return q.load_aggregated()


def _get_df(df):
    return df if df is not None else _load_df()


def render_role_selector(key="role_select"):
    options = list_roles()
    labels = [name for _, name in options]
    ids = [rid for rid, _ in options]
    선택라벨 = st.selectbox("역할 선택", labels, key=key)
    return ids[labels.index(선택라벨)]


def _pct_text(v):
    """증감률(%)을 '▲ 1.23%' / '▼ 1.23%' / '-' 로 표시한다."""
    if v is None:
        return "-"
    return f"▲ {v}%" if v > 0 else (f"▼ {abs(v)}%" if v < 0 else "0%")


def render_summary_box(dff, role_id, date_from, date_to, full_df=None):
    """역할별 핵심 요약을 '큰 숫자 카드 + 한줄 코멘트'로 보여준다.
    full_df: 날짜로 거르기 전 전체 집계본 (경영진 요약의 전월 비교용)."""
    with st.container(border=True):
        if role_id == "executive":
            st.markdown("#### 🏢 경영 요약 (월간)")
            ex = q.executive_summary(full_df if full_df is not None else dff,
                                     str(date_to)[:7])
            if ex.get("status") != "ok":
                st.info(ex.get("message", "요약할 데이터가 없습니다."))
                return
            cur, ch = ex["current"], ex["change"] or {}

            def delta(key):
                # 앞의 +/- 부호로 화살표 방향이 정해진다. 증가=빨강(inverse)
                v = ch.get(key)
                return f"{v:+.2f}% 전월 대비" if v is not None else None

            c1, c2, c3 = st.columns(3)
            c1.metric(f"💡 {ex['month']} 전력량", f"{cur['energy']:,.0f} kWh",
                      delta("energy_pct"), delta_color="inverse")
            c2.metric("💰 요금", f"{cur['bill']:,.0f} 원",
                      delta("bill_pct"), delta_color="inverse")
            c3.metric("🌍 탄소", f"{cur['carbon']:,.0f} kg",
                      delta("carbon_pct"), delta_color="inverse")
            if ch:
                st.caption(f"👉 일평균 기준 전월 대비 {_pct_text(ch.get('daily_pct'))} — "
                           f"총량 증감에는 달마다 다른 날짜 수 차이가 섞여 있어, "
                           f"실제 사용 수준 변화는 일평균으로 판단하세요.")
            else:
                st.caption(f"👉 {ex['month']}은 데이터의 첫 달이라 전월 비교가 없습니다.")
            st.caption(f"👉 감축 우선 검토 대상(탄소 상위): {', '.join(ex['reduction_candidates'])}")
            return
        if role_id == "equipment_manager":
            st.markdown("#### ⚙️ 설비 요약")
            an = q.detect_power_anomalies(dff, date_from, date_to, sigma=3.0, top=100)
            pf = q.get_anomalies(dff, date_from, date_to)
            top_eq = an["items"][0]["equipment"] if an.get("items") else "-"
            c1, c2, c3 = st.columns(3)
            c1.metric("🚨 전력 이상 후보", f"{an.get('count', 0):,} 건")
            c2.metric("⚡ 역률 저하 구간", f"{pf.get('count', 0):,} 건")
            c3.metric("🔧 가장 주의할 설비", top_eq)
            if an.get("items"):
                t = an["items"][0]
                st.caption(f"👉 {t['equipment']} — {t['time']}, 평소 대비 {t['ratio']}배 "
                           f"{t['direction']}. 현장 점검 권장 (고장 단정 아님).")
        elif role_id == "energy_manager":
            st.markdown("#### 🌱 에너지 요약")
            tot = q.get_total_energy(dff, date_from, date_to)
            rank = q.rank_carbon_by_equipment(dff, date_from, date_to, top=1)
            top_eq = rank["items"][0]["equipment"] if rank.get("items") else "-"
            c1, c2, c3 = st.columns(3)
            c1.metric("💡 총 전력량", f"{tot.get('energy', 0):,.0f} kWh")
            c2.metric("💰 요금", f"{tot.get('bill', 0):,.0f} 원")
            c3.metric("🌍 탄소", f"{tot.get('carbon', 0):,.0f} kg")
            st.caption(f"🌳 이 탄소량은 소나무 약 {tot.get('trees', 0):,.0f}그루가 "
                       f"1년간 흡수하는 양입니다. (산림청 기준: 1그루 연 6.6kg)")
            st.caption(f"👉 탄소 최다 배출 설비: {top_eq} — 감축 우선 검토 대상")


def render_status_board(dff, date_from, date_to):
    """설비 13개를 정상/주의/이상 색상 카드로 한눈에 보여준다 (신호등)."""
    board = q.equipment_status_board(dff, date_from, date_to)
    items = board.get("items", [])
    if not items:
        st.info("표시할 설비가 없습니다.")
        return

    # 상태별 색/이모지
    color = {"이상": "#e74c3c", "주의": "#f1c40f", "정상": "#2ecc71"}
    emoji = {"이상": "🔴", "주의": "🟡", "정상": "🟢"}

    # 상태 요약 카운트
    n_bad = sum(1 for i in items if i["status"] == "이상")
    n_warn = sum(1 for i in items if i["status"] == "주의")
    n_ok = sum(1 for i in items if i["status"] == "정상")
    st.markdown(f"**설비 상태 현황판** — 🔴 이상 {n_bad} · 🟡 주의 {n_warn} · 🟢 정상 {n_ok}")
    st.caption(
        "판정 기준 ▸ "
        "🔴 이상 = 전력이 평소 평균에서 ±3σ(표준편차 3배) 벗어난 구간이 있음 · "
        "🟡 주의 = 역률 최저값이 70 미만인 구간이 있음 · "
        "🟢 정상 = 둘 다 없음"
    )

    # 설비 카드를 한 줄에 4개씩 배열
    cols_per_row = 4
    for i in range(0, len(items), cols_per_row):
        row = items[i:i + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, it in zip(cols, row):
            c = color[it["status"]]
            e = emoji[it["status"]]
            with col:
                st.markdown(
                    f"""<div style="border-left:6px solid {c};
                    background:#f7f7f9; border-radius:6px;
                    padding:8px 10px; margin-bottom:8px;">
                    <div style="font-weight:700; color:#1a1a1a;">{e} {it['equipment']}</div>
                    <div style="font-size:0.85em; color:#444;">
                    상태: {it['status']}<br>
                    이상 {it['anomaly_cnt']}건 · 역률저하 {it['pf_low_cnt']}건<br>
                    평균전력 {it['ap_mean']}kW</div>
                    </div>""",
                    unsafe_allow_html=True,
                )


# ------------------------------------------------------------
# 화면 1 - 맞춤 정보 (날짜/설비 필터 + 이상치 시각화)
# ------------------------------------------------------------
def render_custom_info(df=None):
    st.header("맞춤 정보")

    df = _get_df(df)
    if df is None:
        st.error("집계본(d_aggregated.csv)을 찾을 수 없습니다. 먼저 d_preprocess.py를 실행하세요.")
        return

    role_id = render_role_selector(key="custom_info_role")
    role = get_role(role_id)
    st.caption(f"선택한 역할: {role['name']} · 관심지표: {', '.join(role['metrics_kr'])}")

    # -------- 공통 필터 (날짜 + 설비) --------
    equipments = sorted(df["module(equipment)"].unique().tolist())
    dmin, dmax = q.date_range(df)

    f1, f2, f3 = st.columns([2, 2, 3])
    date_from = f1.text_input("시작일 (YYYY-MM-DD)", value=dmin, key="ci_from")
    date_to = f2.text_input("종료일 (YYYY-MM-DD)", value=dmax, key="ci_to")
    sel = f3.selectbox("설비 선택", ["(전체)"] + equipments, key="eq_sel")
    st.caption(f"데이터 기간: {dmin} ~ {dmax}")

    dff = q.filter_by_date(df, date_from, date_to)
    if len(dff) == 0:
        st.warning("선택한 날짜 범위에 데이터가 없습니다. 기간을 확인하세요.")
        return
    st.divider()

    # -------- 역할별 핵심 요약 박스 (맨 위에 한눈 요약) --------
    render_summary_box(dff, role_id, date_from, date_to, full_df=df)
    st.divider()

    # =========================================================
    if role_id == "equipment_manager":
        st.subheader("⚙️ 설비 상태 / 이상")

        an = q.detect_power_anomalies(dff, sigma=3.0, top=100)

        if sel == "(전체)":
            # 설비 13개 신호등 현황판 (정상/주의/이상)
            render_status_board(dff, date_from, date_to)
            st.divider()

            st.markdown("#### 🚨 전력 이상치 탐지 (설비별 평소 대비 3σ)")
            st.metric("이상 후보 구간 수 (선택 기간)", f"{an['count']:,}")
            st.caption(
                "기준 ▸ 각 설비의 평소 전력 평균을 구하고, 거기서 "
                "'표준편차 × 3'(=3σ)보다 더 벗어난 시간대를 이상 후보로 봅니다. "
                "쉽게 말해 '평소와 통계적으로 크게 다른 구간'입니다. "
                "※ '고장'이 아니라 '확인이 필요한 관찰 사실'입니다."
            )
            if an["items"]:
                adf = pd.DataFrame(an["items"])[
                    ["equipment", "time", "power", "normal_mean", "ratio", "direction"]]
                adf.columns = ["설비", "발생 시각", "전력(kW)", "평소평균", "평소대비(배)", "방향"]
                st.dataframe(adf.head(30), use_container_width=True)
                top1 = an["items"][0]
                st.info(f"가장 크게 벗어난 구간: **{top1['equipment']}** — {top1['time']}, "
                        f"전력 {top1['power']}kW (평소 {top1['normal_mean']}kW의 "
                        f"{top1['ratio']}배, {top1['direction']}). "
                        f"고장으로 단정할 수 없으며 현장 점검이 필요합니다.")
            with st.expander("참고: 역률이 낮았던 구간 (노후화 의심)"):
                a = q.get_anomalies(dff)
                st.caption(f"역률 70 미만 구간 수: {a['count']:,}")
                if a["items"]:
                    rdf = pd.DataFrame(a["items"])[["equipment", "time", "pf_min", "ap_max"]]
                    rdf.columns = ["설비", "발생 시각", "역률 최저", "최대전력"]
                    st.dataframe(rdf.head(20), use_container_width=True)

        else:
            # ----- 특정 설비: 수치 + 그래프 + 이상 시점 -----
            p = q.get_equipment_power(dff, sel)
            if p.get("status") != "ok":
                st.warning(p.get("message", "데이터가 없습니다."))
                return
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("평균 전력(kW)", f"{p['ap_mean']:,.1f}")
            c2.metric("최대 전력(kW)", f"{p['ap_max']:,.1f}")
            c3.metric("역률 최저", f"{p['pf_min']:,.1f}")
            c4.metric("가동률", f"{p['op_ratio']*100:,.0f}%")
            # 전압·전류 순간 피크 (멘토 제시 컬럼 반영)
            c5, c6, c7, c8 = st.columns(4)
            c5.metric("전압 최대(R상)", f"{p['vR_max']:,.1f}")
            c6.metric("전류 최대(R상)", f"{p['cR_max']:,.1f}")
            c7.metric("전류 최대(S상)", f"{p['cS_max']:,.1f}")
            c8.metric("전류 최대(T상)", f"{p['cT_max']:,.1f}")
            st.caption(f"대상: {sel} · 집계 시간수 {p['hours']:,} · "
                       f"전압/전류 '최대'는 기간 중 순간 피크")

            sub = dff[dff["module(equipment)"] == sel].sort_values("hour").copy()
            sub["시각"] = sub["hour"].map(q.hour_to_text)

            eq_anoms = [it for it in an["items"] if it["equipment"] == sel]

            # (1) 전력 추이 + 이상 시점
            st.markdown("#### 전력 추이 (평균/최대)")
            chart = sub.set_index("시각")[["ap_mean", "ap_max"]]
            chart.columns = ["평균전력", "최대전력"]
            st.line_chart(chart)
            if eq_anoms:
                st.markdown("**🚨 이상치가 관측된 시점**")
                e_df = pd.DataFrame(eq_anoms)[
                    ["time", "power", "normal_mean", "ratio", "direction"]]
                e_df.columns = ["발생 시각", "전력(kW)", "평소평균", "평소대비(배)", "방향"]
                st.dataframe(e_df, use_container_width=True)
                for it in eq_anoms[:3]:
                    st.warning(f"⚠️ {it['time']} — 전력이 평소 {it['normal_mean']}kW의 "
                               f"**{it['ratio']}배**({it['power']}kW, {it['direction']}). "
                               f"같은 시각의 전압·전류·역률을 함께 점검하세요.")
            else:
                st.success("선택 기간에 이 설비의 전력 이상치(3σ)는 없습니다.")

            # (2) 전압 추이 (3상 평균 + 최대) — 순간 피크 확인
            st.markdown("#### 전압 추이 (3상 평균 / 최대)")
            vchart = sub.set_index("시각")[["vR_mean", "vS_mean", "vT_mean", "vR_max"]]
            vchart.columns = ["R상(평균)", "S상(평균)", "T상(평균)", "R상(최대·피크)"]
            st.line_chart(vchart)

            # (2-1) 역률 추이 (평균 / 최저) — 70 기준선 표시
            st.markdown("#### 역률 추이 (평균 / 최저)")
            figpf = go.Figure()
            figpf.add_trace(go.Scatter(
                x=sub["시각"], y=sub["pf_mean"], mode="lines",
                name="역률 평균", line=dict(color="#1f77b4"),
            ))
            figpf.add_trace(go.Scatter(
                x=sub["시각"], y=sub["pf_min"], mode="lines",
                name="역률 최저", line=dict(color="#ff7f0e"),
            ))
            # 역률 70 경고 기준선
            figpf.add_hline(y=70, line_dash="dash", line_color="red",
                            annotation_text="주의 기준 70", annotation_position="bottom right")
            figpf.update_layout(
                height=320, margin=dict(l=10, r=10, t=30, b=10),
                xaxis_title="시각", yaxis_title="역률(%)",
                hovermode="x unified", legend=dict(orientation="h", y=1.15),
            )
            st.plotly_chart(figpf, use_container_width=True)
            st.caption("※ 역률이 낮을수록 전력 효율이 떨어집니다. "
                       "빨간 점선(70) 아래로 내려가면 노후화·이상을 의심해 점검하세요.")

            # (3) 전류 추이 — 정상(초록)/이상(빨강) 강조 스타일
            st.markdown("#### 전류 추이 (R상 최대) — 정상/이상 강조")

            anom_hours = {it["hour"] for it in eq_anoms}  # 이상 시각 집합
            sub2 = sub.copy()
            sub2["is_anom"] = sub2["hour"].isin(anom_hours)

            fig = go.Figure()
            # 1) 전체 선은 초록(정상 흐름)
            fig.add_trace(go.Scatter(
                x=sub2["시각"], y=sub2["cR_max"], mode="lines",
                name="전류 R상(정상 흐름)", line=dict(color="#2ca02c", width=1.5),
            ))
            # 2) 이상 지점만 빨간 점으로 덧칠
            anom_pts = sub2[sub2["is_anom"]]
            if len(anom_pts) > 0:
                fig.add_trace(go.Scatter(
                    x=anom_pts["시각"], y=anom_pts["cR_max"], mode="markers",
                    name="🚨 이상 지점", marker=dict(color="red", size=9, symbol="circle"),
                ))
            fig.update_layout(
                height=380, margin=dict(l=10, r=10, t=30, b=10),
                xaxis_title="시각", yaxis_title="전류 최대(A)",
                hovermode="x unified", legend=dict(orientation="h", y=1.12),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption("※ 초록 선 = 전류 흐름, 빨간 점 = 전력 이상치가 발생한 시각의 전류. "
                       "빨간 점이 평소 흐름에서 벗어나 있으면 그 시각을 함께 점검하세요.")

            # --- [되돌리기용 백업] 이전 '3상 빨간 세로선' 버전 ---
            # fig = go.Figure()
            # x = sub["시각"]
            # fig.add_trace(go.Scatter(x=x, y=sub["cR_max"], mode="lines", name="R상(최대)", line=dict(color="#1f77b4")))
            # fig.add_trace(go.Scatter(x=x, y=sub["cS_max"], mode="lines", name="S상(최대)", line=dict(color="#2ca02c")))
            # fig.add_trace(go.Scatter(x=x, y=sub["cT_max"], mode="lines", name="T상(최대)", line=dict(color="#ff7f0e")))
            # anom_times = {it["time"] for it in eq_anoms}; x_list = list(x)
            # for t in anom_times:
            #     if t in x_list: fig.add_vline(x=x_list.index(t), line_width=2, line_dash="dash", line_color="red")
            # st.plotly_chart(fig, use_container_width=True)

    # =========================================================
    elif role_id == "energy_manager":
        st.subheader("🌱 비용 / 탄소 요약")

        total = q.get_total_energy(dff)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 전력량(kWh)", f"{total['energy']:,.0f}")
        c2.metric("총 요금(원)", f"{total['bill']:,.0f}")
        c3.metric("총 탄소(kgCO₂)", f"{total['carbon']:,.0f}")
        c4.metric("🌳 소나무 환산", f"{total.get('trees', 0):,.0f} 그루")
        st.caption(f"요금=전력량×{BILL_RATE}, 탄소=전력량×{CARBON_RATE} "
                   f"· 기간 {date_from}~{date_to}")
        st.caption(f"🌳 소나무 환산: 위 탄소량은 소나무 약 {total.get('trees', 0):,.0f}그루가 "
                   f"1년간 흡수하는 양입니다. (산림청 기준: 30년생 소나무 1그루 연 6.6kg CO₂ 흡수)")

        st.divider()
        if sel == "(전체)":
            st.write("**탄소 배출 상위 설비 (감축 대상 후보)**")
            r = q.rank_carbon_by_equipment(dff, top=13)
            rank_df = pd.DataFrame(r["items"])
            st.dataframe(rank_df, use_container_width=True)
            st.bar_chart(rank_df.set_index("equipment")["carbon"])
        else:
            e = q.get_equipment_energy(dff, sel)
            st.write(f"**{sel}** 전력량 / 요금 / 탄소")
            c1, c2, c3 = st.columns(3)
            c1.metric("전력량(kWh)", f"{e['energy']:,.0f}")
            c2.metric("요금(원)", f"{e['bill']:,.0f}")
            c3.metric("탄소(kgCO₂)", f"{e['carbon']:,.0f}")
            sub = dff[dff["module(equipment)"] == sel].copy()
            sub["날짜"] = sub["hour"].map(q.hour_to_date)
            daily = sub.groupby("날짜")["kwh_sum"].sum()
            st.markdown("#### 일별 전력량 추이")
            st.bar_chart(daily)

        # ---- 전월 대비 비교 (전체 기간 기준, 날짜 필터와 무관) ----
        st.divider()
        st.markdown("#### 📊 월별 비교 (전월 대비 증감)")
        full_df = _get_df(df)
        eq_arg = None if sel == "(전체)" else sel
        mc = q.monthly_compare(full_df, equipment=eq_arg)
        if mc.get("items"):
            mdf = pd.DataFrame(mc["items"])
            # 증감률을 보기 좋게 표시용 컬럼으로
            mdf_disp = mdf.copy()
            mdf_disp["kwh_diff_pct"] = mdf_disp["kwh_diff_pct"].map(
                lambda v: "-" if v is None else (f"▲ {v}%" if v > 0 else f"▼ {abs(v)}%")
            )
            mdf_disp.columns = ["월", "전력량(kWh)", "요금(원)", "탄소(kg)", "전월대비"]
            st.dataframe(mdf_disp, use_container_width=True)
            # 월별 전력량 막대그래프
            st.bar_chart(mdf.set_index("month")["kwh"])
            st.caption("※ '전월대비'는 전력량 기준 증감률입니다. "
                       f"대상: {'전체 설비' if eq_arg is None else eq_arg}")

    # =========================================================
    elif role_id == "executive":
        render_executive_section(df, date_to)


def render_executive_section(df, date_to):
    """경영진(페르소나 3) 화면: 월 단위 결론 → 원인 설비 → 감축·점검 권고 순서."""
    st.subheader("🏢 월간 경영 보고")
    st.caption("경영진 화면은 월 단위 · 전체 설비 기준입니다. (위 설비 선택과 무관)")

    months = [it["month"] for it in q.monthly_compare(df).get("items", [])]
    if not months:
        st.info("월별 데이터가 없습니다.")
        return
    default = str(date_to)[:7]
    month = st.selectbox("보고 월", months,
                         index=months.index(default) if default in months else len(months) - 1,
                         key="exec_month")
    ex = q.executive_summary(df, month)
    if ex.get("status") != "ok":
        st.info(ex.get("message", "요약할 데이터가 없습니다."))
        return

    # (1) 핵심 수치: 이번 달 vs 전월
    st.markdown("#### 1. 핵심 수치 — 전월 대비")
    cur, prev, ch = ex["current"], ex["previous"], ex["change"]
    if prev:
        rows = [
            ["전력량(kWh)", f"{cur['energy']:,.0f}", f"{prev['energy']:,.0f}", _pct_text(ch["energy_pct"])],
            ["요금(원)", f"{cur['bill']:,.0f}", f"{prev['bill']:,.0f}", _pct_text(ch["bill_pct"])],
            ["탄소(kgCO₂)", f"{cur['carbon']:,.0f}", f"{prev['carbon']:,.0f}", _pct_text(ch["carbon_pct"])],
            ["일평균 전력량(kWh/일)", f"{cur['daily_energy']:,.0f}", f"{prev['daily_energy']:,.0f}",
             _pct_text(ch["daily_pct"])],
        ]
        st.dataframe(pd.DataFrame(rows, columns=["항목", ex["month"], ex["prev_month"], "전월 대비"]),
                     use_container_width=True, hide_index=True)
        st.caption(f"측정 시간: {ex['month']} {cur['hours']:,.0f}시간 · {ex['prev_month']} "
                   f"{prev['hours']:,.0f}시간. 총량 증감은 날짜 수 차이를 포함하므로, "
                   f"사용 수준이 실제로 바뀌었는지는 '일평균' 줄로 판단하세요.")
    else:
        st.info(f"{ex['month']}은 데이터의 첫 달이라 전월 비교가 없습니다. "
                f"전력량 {cur['energy']:,.0f} kWh · 요금 {cur['bill']:,.0f}원 · 탄소 {cur['carbon']:,.0f} kg")
    st.caption("※ 전년 대비는 데이터가 2024-12 ~ 2025-04 다섯 달뿐이라 제공할 수 없습니다.")

    # (2) 월별 탄소 추이 (일평균 기준 포함)
    st.markdown("#### 2. 월별 탄소 추이")
    trend = []
    for m in months:
        e = q.executive_summary(df, m)
        if e.get("status") == "ok":
            days = e["current"]["hours"] / 24 if e["current"]["hours"] else 0
            trend.append({"월": m, "탄소(kg)": e["current"]["carbon"],
                          "일평균 탄소(kg/일)": round(e["current"]["carbon"] / days, 1) if days else 0})
    tdf = pd.DataFrame(trend).set_index("월")
    t1, t2 = st.columns(2)
    with t1:
        st.caption("월 총량")
        st.bar_chart(tdf["탄소(kg)"])
    with t2:
        st.caption("일평균 (날짜 수 차이 제거)")
        st.bar_chart(tdf["일평균 탄소(kg/일)"])

    # (3) 설비별 배출 순위 · 비중 → 감축 우선 검토 대상
    st.markdown("#### 3. 설비별 탄소 배출 순위 · 비중")
    rk = pd.DataFrame(ex["carbon_ranking"])
    rk_disp = pd.DataFrame({
        "순위": range(1, len(rk) + 1),
        "설비": rk["equipment"],
        "탄소(kg)": rk["carbon"].map(lambda v: f"{v:,.0f}"),
        "비중(%)": rk["share_pct"],
        "전월 대비(일평균)": rk["daily_change_pct"].map(_pct_text),
    })
    st.dataframe(rk_disp, use_container_width=True, hide_index=True)
    st.success(f"감축 우선 검토 대상 (탄소 상위 {len(ex['reduction_candidates'])}개): "
               f"{', '.join(ex['reduction_candidates'])}")
    if ex.get("share_spread_pctp") is not None:
        st.caption(f"※ 설비 간 배출 비중 차이는 최대 {ex['share_spread_pctp']}%p입니다. "
                   f"차이가 작을수록 순위는 달마다 바뀔 수 있으니, 투자 판단에는 "
                   f"여러 달의 순위와 아래 이상 징후를 함께 보세요.")
    drv = ex.get("increase_driver")
    if drv:
        st.warning(f"📈 전월 대비 탄소 증가 기여가 가장 큰 설비: **{drv['equipment']}** — "
                   f"일평균 탄소 {drv['daily_carbon_change']:+,.1f} kg/일 "
                   f"({_pct_text(drv['daily_change_pct'])})")
    elif prev:
        st.info("전월보다 일평균 탄소가 늘어난 설비가 없습니다.")

    # (4) 이상 징후 설비 → 에너지 낭비·탄소 증가 가능성 (점검 권고)
    st.markdown("#### 4. 이상 징후 설비 — 점검 권고")
    # 역률 저하 설비는 매달 반복되는 경우가 많아 초과 전력량 순서와 상관없이 먼저 보여준다
    watch = ([w for w in ex["watch_equipment"] if w["pf_low_hours"] > 0]
             + [w for w in ex["watch_equipment"] if w["pf_low_hours"] == 0])
    if not watch:
        st.success("이번 달에 관찰된 이상 징후가 없습니다.")
    for w in watch[:6]:
        lines = []
        if w["high_cnt"] > 0:
            lines.append(f"전력이 평소보다 높았던 시간 {w['high_cnt']}건 · 초과 전력량 약 "
                         f"{w['excess_kwh']:,.0f} kWh (요금 약 {w['excess_bill']:,.0f}원, "
                         f"탄소 약 {w['excess_carbon']:,.0f} kg) → 에너지 낭비·탄소 증가 가능성")
        elif w["anomaly_cnt"] > 0:
            lines.append(f"전력이 평소보다 낮았던 시간 {w['anomaly_cnt']}건 → 가동 상태 확인 필요")
        if w["pf_low_hours"] > 0:
            lines.append(f"역률 70 미만 {w['pf_low_hours']:,}시간 → 전기 효율 저하 가능성")
        st.warning(f"⚠️ **{w['equipment']}** — " + " / ".join(lines) + ". 점검 권고 (고장 단정 아님)")
    if len(watch) > 6:
        st.caption(f"외 {len(watch) - 6}개 설비는 맞춤 정보(설비 관리 담당자) 화면에서 확인하세요.")
    st.caption("※ 이상 징후는 전력 3σ · 역률 70 미만 기준으로 관찰된 사실입니다. "
               "점검 승인 여부는 현장 확인 결과와 함께 판단하세요.")


# ------------------------------------------------------------
# 화면 2 - AI 요약 보고서 / 상담
# ------------------------------------------------------------
def render_ai_report(df=None):
    st.header("AI 상담")

    df = _get_df(df)
    role_id = render_role_selector(key="ai_report_role")
    role = get_role(role_id)
    st.caption(f"{role['name']} 관점으로 요약·응답합니다.")
    st.divider()

    question = st.text_input("질문을 입력하세요",
                             placeholder="예) 전력을 가장 많이 쓴 설비는? / 총 요금이 얼마야?")

    if st.button("질문하기"):
        if not question.strip():
            st.warning("질문을 입력해 주세요.")
            return

        try:
            api_key = st.secrets.get("OPENAI_API_KEY")
        except Exception:
            api_key = None

        import d_ai
        with st.spinner("AI가 데이터를 조회하는 중..."):
            # 팀 합의 모델(d_ai.MODEL_NAME = gpt-4o-mini) 사용
            res = d_ai.ask(question, role_id=role_id, df=df, api_key=api_key,
                           model=d_ai.MODEL_NAME)

        if res.get("status") == "no_api_key":
            st.warning("OpenAI API 키가 설정되지 않았습니다. "
                       ".streamlit/secrets.toml 에 OPENAI_API_KEY를 넣어주세요.")
            st.caption("키가 없어도 '맞춤 정보' 탭의 조회·계산은 바로 쓸 수 있습니다.")
        elif res.get("status") == "ok":
            st.markdown("### 🤖 AI 해석")
            st.write(res["answer"])
            if res.get("used_tools"):
                with st.expander("📊 계산 결과 (코드가 조회한 숫자 — 근거)"):
                    st.json(res["used_tools"])
        else:
            st.error(f"응답 상태: {res.get('status')} / {res.get('message','')}")


# ------------------------------------------------------------
# 혼자 테스트 (A 대시보드에서는 이 부분 실행 안 됨)
#   터미널: streamlit run d_screens.py
# ------------------------------------------------------------
if __name__ == "__main__":
    st.set_page_config(page_title=PROJECT_TITLE, layout="wide")
    render_header()
    탭1, 탭2 = st.tabs(["맞춤 정보", "AI 상담"])
    with 탭1:
        render_custom_info()
    with 탭2:
        render_ai_report()
