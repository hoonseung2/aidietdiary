import streamlit as st
import streamlit_authenticator as stauth
import pandas as pd
from sqlalchemy import create_engine, text
from google import genai
from PIL import Image
import plotly.express as px
import os
from dotenv import load_dotenv
import time
import yaml
from yaml.loader import SafeLoader


with open('config.yaml', encoding='utf-8') as file:
    config = yaml.load(file, Loader=SafeLoader)

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

st.set_page_config(page_title="AI 식단 관리자", layout="wide")

@st.cache_resource
def init_connection():
    load_dotenv()
    engine = create_engine("sqlite:///diet_diary.db")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS diet_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                food_name TEXT,
                calories REAL,
                protein REAL,
                fat REAL,
                carbs REAL,
                created_at DATETIME DEFAULT (DATETIME('now', 'localtime'))
            )
        """))
        conn.commit()
    return engine

@st.cache_resource
def get_ai_client():
    api_key = os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=api_key)

engine = init_connection()
client = get_ai_client()


st.sidebar.title("🔐 계정 관리")
menu = ["로그인", "회원가입"]
choice = st.sidebar.selectbox("메뉴", menu)


if choice == "회원가입":
    try:
        if authenticator.register_user(location='main'):
            st.success('회원가입 성공! 이제 로그인 메뉴로 이동하세요.')
            with open('config.yaml', 'w', encoding='utf-8') as file:
                yaml.dump(config, file, default_flow_style=False)
    except Exception as e:
        st.error(f"회원가입 중 오류 발생: {e}")


elif choice == "로그인":
    authenticator.login(location='main')

    if st.session_state["authentication_status"]:
        name = st.session_state["name"]
        username = st.session_state["username"]
        
        st.sidebar.write(f"👋 **{name}**님 반가워요!")
        authenticator.logout("로그아웃", "sidebar")

        st.title("🥗 AI 음식 인식 및 식단 일기")
        
        # 오늘의 요약
        summary_query = text("""
            SELECT SUM(calories) as cal, SUM(protein) as prot FROM diet_logs
            WHERE DATE(created_at) = DATE('now', 'localtime') AND user_id = :uid
        """)
        summary = pd.read_sql(summary_query, con=engine, params={"uid": username})
        
        st.sidebar.header("📊 오늘의 요약")
        st.sidebar.metric("총 칼로리", f"{summary['cal'][0] or 0} kcal")
        st.sidebar.metric("총 단백질", f"{summary['prot'][0] or 0} g")

  
        col_chart, col_pie = st.columns(2)

        with col_chart:
            st.subheader("📅 최근 7일 추이")
            chart_query = text("""
                SELECT DATE(created_at) as date, SUM(calories) as daily_cal 
                FROM diet_logs WHERE user_id = :uid
                GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 7
            """)
            chart_df = pd.read_sql(chart_query, con=engine, params={"uid": username})
            if not chart_df.empty:
                fig = px.line(chart_df.sort_values('date'), x='date', y='daily_cal', markers=True)
                st.plotly_chart(fig, width='stretch')
            else:
                st.info("기록을 시작하면 차트가 나타납니다.")

        with col_pie:
            st.subheader("🍕 오늘 영양소 비율")
            ratio_query = text("""
                SELECT SUM(carbs) as carbs, SUM(protein) as protein, SUM(fat) as fat 
                FROM diet_logs WHERE DATE(created_at) = DATE('now', 'localtime') AND user_id = :uid
            """)
            ratio_df = pd.read_sql(ratio_query, con=engine, params={"uid": username})
            if not ratio_df.empty and ratio_df.iloc[0].sum() > 0:
                melted_df = ratio_df.melt(var_name='영양소', value_name='g')
                pie_fig = px.pie(melted_df, values='g', names='영양소', hole=0.3)
                st.plotly_chart(pie_fig, width='stretch')
            else:
                st.info("오늘의 데이터가 없습니다.")

        st.markdown("---")

 
        uploaded_file = st.file_uploader("음식 사진을 올려주세요...", type=["jpg", "jpeg", "png"])
        
        if uploaded_file:
            img = Image.open(uploaded_file)
            st.image(img, caption="업로드된 사진", width=300)
            
            if "result_df" not in st.session_state or st.session_state.get("last_uploaded") != uploaded_file.name:
                with st.spinner("AI 분석 중..."):

                    prompt = """
                    너는 음식 인식 전문가야. 사진을 분석해서 규칙대로 답해.
                    1. 음식의 표준 명칭 1개와 관련 키워드 2개를 찾아내.
                    2. 반드시 '단어, 단어, 단어' 형식으로만 출력해.
                    3. 설명이나 문장은 절대 포함하지 마.
                    예: 돈까스, 고기튀김, 커틀릿
                    """
                    try:
                    # 2026년 기준 최신 모델명 사용
                        response = client.models.generate_content(
                        model="gemini-flash-latest",
                        contents=[prompt, img]
                        )
                    except Exception as e:
                        # 429 에러(Quota Exceeded) 처리
                        if "429" in str(e):
                            st.warning("⚠️ 현재 무료 API 할당량을 모두 소모했습니다. 약 1분 후 다시 시도해주세요.")
                        # 기타 에러 처리
                        else:
                            st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
    
                        # 에러 발생 시 이후 로직(데이터베이스 저장 등)이 실행되지 않도록 중단
                        st.stop()                

                    raw_text = response.text.strip().replace('\n', ',')
                    keywords = [k.strip() for k in raw_text.split(',') if k.strip()]
                    
                    st.write(f"🔎 추출 키워드: {', '.join(keywords)}")

                    all_results = []
                    with engine.connect() as conn:
                        for word in keywords:

                            clean_word = "".join(filter(str.isalnum, word))
                            query = text("SELECT * FROM food_metadata WHERE food_name LIKE :word LIMIT 5")
                            temp_df = pd.read_sql(query, con=conn, params={"word": f"%{clean_word}%"})
                            if not temp_df.empty:
                                all_results.append(temp_df)
                    
                    if all_results:
                        st.session_state["result_df"] = pd.concat(all_results).drop_duplicates(subset=['food_name'])
                    else:
                        st.session_state["result_df"] = pd.DataFrame()

                    st.session_state["last_uploaded"] = uploaded_file.name
                    st.session_state["keywords"] = keywords

            result_df = st.session_state["result_df"]
            if not result_df.empty:
                food_options = [f"{row['food_name']} ({row['calories']}kcal)" for _, row in result_df.iterrows()]
                selected_option = st.radio("가장 가까운 식품을 선택하세요:", food_options)
                
                if st.button("📌 식단 기록하기"):
                    best_match = result_df.iloc[food_options.index(selected_option)]
                    with engine.connect() as conn:
                        conn.execute(text("""
                            INSERT INTO diet_logs (user_id, food_name, calories, protein, fat, carbs)
                            VALUES (:uid, :name, :cal, :prot, :fat, :carb)
                        """), {
                            "uid": username, "name": best_match['food_name'],
                            "cal": round(float(best_match['calories']), 1),
                            "prot": round(float(best_match['protein']), 1),
                            "fat": round(float(best_match['fat']), 1),
                            "carb": round(float(best_match['carbs']), 1)
                        })
                        conn.commit()
                    st.success("✅ 기록 완료!")
                    st.balloons()
                    time.sleep(1)
                    st.rerun()
            else:
                st.error("검색 결과가 없습니다. 다른 사진을 시도해 보세요.")

    elif st.session_state["authentication_status"] is False:
        st.error('아이디 또는 비밀번호가 잘못되었습니다.')
    elif st.session_state["authentication_status"] is None:
        st.info('왼쪽 사이드바에서 로그인해 주세요.')