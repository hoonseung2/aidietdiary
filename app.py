import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
from google import genai
from PIL import Image
import plotly.express as px
import os
from dotenv import load_dotenv
from sqlalchemy import text


load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

engine = create_engine("sqlite:///diet_diary.db")
client = genai.Client(api_key=API_KEY)
with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS diet_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            food_name TEXT,
            calories REAL,
            protein REAL,
            fat REAL,
            carbs REAL,
            created_at DATETIME DEFAULT (DATETIME('now', 'localtime'))
        )
    """))
    conn.commit()
    
prompt = """
너는 전문 영양사야. 사진 속 음식을 보고 한국 식품안전관리인증원 DB에 검색하기 가장 좋은 표준 명칭으로 대답해줘. 예를 들어 '돈가스'보다는 '돈까스', '제육'보다는 '제육볶음'이라고 대답해
이 사진의 음식을 분석해서:
1. 가장 가능성 높은 이름 1개
2. 검색에 도움될만한 연관 키워드 2개
를 쉼표로 구분해서 한글로만 알려줘. (예: 돈까스, 고기튀김, 커틀릿)
"""
st.set_page_config(page_title="AI 식단 관리자", layout="wide")
st.title("🥗 AI 음식 인식 및 식단 일기")

#사이드바
st.sidebar.header("📊 오늘의 요약")
summary_query = "SELECT SUM(calories) as cal, SUM(protein) as prot FROM diet_logs WHERE DATE(created_at) = DATE('now', 'localtime')"
summary = pd.read_sql(summary_query, con=engine)
st.sidebar.metric("총 칼로리", f"{summary['cal'][0] or 0} kcal")
st.sidebar.metric("총 단백질", f"{summary['prot'][0] or 0} g")

st.markdown("---")
st.subheader("📅 최근 7일간 영양 섭취 추이")

#최근 7일치 데이터
chart_query = """
    SELECT DATE(created_at) as date, SUM(calories) as daily_cal 
    FROM diet_logs 
    GROUP BY DATE(created_at) 
    ORDER BY date DESC 
    LIMIT 7
"""
chart_df = pd.read_sql(chart_query, con=engine)

if not chart_df.empty:
    chart_df = chart_df.sort_values('date')
    
    #선 그래프
    fig = px.line(chart_df, x='date', y='daily_cal', 
                  title='일별 칼로리 섭취량',
                  labels={'date': '날짜', 'daily_cal': '칼로리(kcal)'},
                  markers=True)
    
    #그래프 테마
    fig.update_layout(hovermode="x unified")
    
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("시각화할 데이터가 아직 부족합니다. 식단을 기록해 보세요!")

#탄단지 비율
if not summary.empty and (summary['cal'].fillna(0)[0] > 0):
    st.subheader("🍕 오늘 섭취 영양소 비율")
    
    ratio_query = "SELECT SUM(carbs) as carbs, SUM(protein) as protein, SUM(fat) as fat FROM diet_logs WHERE DATE(created_at) = DATE('now', 'localtime')"
    ratio_df = pd.read_sql(ratio_query, con=engine)
    
    # 데이터 재구성
    melted_df = ratio_df.melt(var_name='영양소', value_name='g')
    
    pie_fig = px.pie(melted_df, values='g', names='영양소', hole=0.3,
                     color_discrete_sequence=px.colors.sequential.RdBu)
    st.plotly_chart(pie_fig)
    
#메인 화면
uploaded_file = st.file_uploader("음식 사진을 올려주세요...", type=["jpg", "jpeg", "png"])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="업로드된 사진", width=300)
    
    if "result_df" not in st.session_state or st.session_state.get("last_uploaded") != uploaded_file.name:
        with st.spinner("AI가 분석 중입니다..."):
            response = client.models.generate_content(model="gemini-flash-latest", contents=[prompt, img])
            keywords = [k.strip() for k in response.text.split(',')]
            
            all_results = []
            for word in keywords:
                temp_query = f"SELECT * FROM food_metadata WHERE food_name LIKE '%%{word}%%' LIMIT 5"
                temp_df = pd.read_sql(temp_query, con=engine)
                all_results.append(temp_df)
            
            if all_results:
                st.session_state["result_df"] = pd.concat(all_results).drop_duplicates(subset=['food_name'])
            else:
                st.session_state["result_df"] = pd.DataFrame()

            st.session_state["last_uploaded"] = uploaded_file.name
            st.session_state["keywords"] = keywords

    result_df = st.session_state["result_df"]
    keywords = st.session_state["keywords"]

    if not result_df.empty:
        st.write(f"🔎 추출 키워드: {', '.join(keywords)}")
        st.success("항목을 선택하고 아래 버튼을 눌러주세요.")
        
        food_options = [f"{row['food_name']} ({row['calories']}kcal)" for _, row in result_df.iterrows()]
        selected_option = st.radio("식품 목록", food_options)
        
        selected_index = food_options.index(selected_option)
        best_match = result_df.iloc[selected_index]
        
        if st.button("📌 이 항목으로 식단 기록하기"):
            log_df = pd.DataFrame([{
                'food_name': best_match['food_name'], 
                'calories': best_match['calories'], 
                'protein': best_match['protein'], 
                'fat': best_match['fat'], 
                'carbs': best_match['carbs']
            }])
            log_df.to_sql(name='diet_logs', con=engine, if_exists='append', index=False)
            st.success("✅ 기록 완료!")
            st.balloons()
            
    else:
        st.error("DB에서 정보를 찾을 수 없습니다.")