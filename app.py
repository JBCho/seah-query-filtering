import streamlit as st
import pandas as pd
import openai
import io

# 페이지 설정
st.set_page_config(page_title="엑셀 자연어 분석기", layout="wide")

st.title("📊 AI 기반 엑셀 데이터 분석 및 추출기")
st.markdown("""
    업로드한 통합 시험 결과 엑셀 파일을 자연어로 검색하고 결과를 다운로드할 수 있습니다.
""")

# --- 1. 사이드바: 설정 ---
with st.sidebar:
    st.header("설정 (Settings)")
    
    # API Key 처리 로직 변경: Secrets에서 먼저 찾고, 없으면 입력창 표시
    api_key = None
    
    if "OPENAI_API_KEY" in st.secrets:
        api_key = st.secrets["OPENAI_API_KEY"]
        st.success("✅ 저장된 API Key를 사용합니다.")
    else:
        api_key = st.text_input("OpenAI API Key", type="password", help="sk-로 시작하는 키를 입력하세요.")
    
    if not api_key:
        st.warning("API Key가 필요합니다. .streamlit/secrets.toml에 설정하거나 입력해주세요.")
        st.stop()
    
    client = openai.OpenAI(api_key=api_key)

# --- 2. 메인: 파일 업로드 ---
uploaded_file = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])

def get_filter_code(df_columns, user_query):
    """
    OpenAI API를 사용하여 자연어를 판다스 필터링 코드로 변환
    """
    # 컬럼 정보 정리 (중복 컬럼 등 포함)
    columns_list = list(df_columns)
    
    prompt = f"""
    You are a Python Data Analyst.
    I have a pandas DataFrame named `df`.
    The columns are: {columns_list}
    
    User Query: "{user_query}"
    
    Task:
    1. Generate a Python code snippet to filter `df` based on the query.
    2. Store the result in a variable named `result_df`.
    3. Handle duplicate column names (like 'C', 'C.1') intelligently. usually '.1' means the second test.
    4. If the query is about sorting, apply sorting.
    5. Output ONLY the python code. No markdown, no explanations.
    
    Example:
    Query: "Find rows where C is greater than 0.05"
    Code: result_df = df[df['C'] > 0.05]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o", # 또는 gpt-3.5-turbo
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        return response.choices[0].message.content.strip().replace("```python", "").replace("```", ""), prompt
    except Exception as e:
        return str(e), prompt

if uploaded_file:
    # --- 3. 데이터 로드 전략 ---
    # 전략: 
    # 1. df_analysis: 분석용 (header=1, 즉 2번째 줄을 컬럼으로 사용)
    # 2. df_raw: 다운로드용 (header=None, 전체를 데이터로 취급)
    
    try:
        # 분석용 데이터 로드 (2번째 행이 실제 컬럼명이라고 가정)
        df_analysis = pd.read_excel(uploaded_file, header=1)
        
        # 원본 구조 유지를 위한 Raw 데이터 로드
        uploaded_file.seek(0)
        df_raw = pd.read_excel(uploaded_file, header=None)
        
        # 미리보기
        with st.expander("📂 업로드된 파일 미리보기 (첫 5행)", expanded=True):
            st.dataframe(df_analysis.head())
            st.caption(f"총 {len(df_analysis)}개의 행과 {len(df_analysis.columns)}개의 열이 감지되었습니다.")

        # --- 4. 쿼리 입력 ---
        st.divider()
        col_q1, col_q2 = st.columns([3, 1])
        with col_q1:
            user_query = st.text_input("🔍 질문을 입력하세요 (예: '탄소(C) 함량이 0.06 이상이고 T방향 연신율이 50 이하인 것 찾아줘')", 
                                     placeholder="자연어로 조건을 설명해주세요.")
        
        with col_q2:
            run_btn = st.button("검색 실행", type="primary", use_container_width=True)

        # --- 5. 결과 처리 ---
        if run_btn and user_query:
            with st.spinner("AI가 데이터를 분석 중입니다..."):
                # 1) 코드 생성
                generated_code, used_prompt = get_filter_code(df_analysis.columns, user_query)
                
                # 디버깅용 코드 표시 (필요 시 주석 처리 가능)
                with st.expander("생성된 파이썬 코드 확인"):
                    st.code(generated_code, language='python')

                # 2) 코드 실행
                local_vars = {'df': df_analysis}
                try:
                    exec(generated_code, {}, local_vars)
                    result_df = local_vars.get('result_df')
                    
                    if result_df is not None and not result_df.empty:
                        st.success(f"검색 완료! {len(result_df)}개의 결과를 찾았습니다.")
                        
                        # --- 6. 결과 조회 및 다운로드 ---
                        st.dataframe(result_df)
                        
                        # 원본 헤더 복원 로직
                        # 분석된 result_df의 인덱스를 사용하여 df_raw에서 해당 행을 가져옴
                        # 0, 1행은 헤더이므로 무조건 포함 + (result_df의 인덱스 + 2) 행을 가져옴
                        header_rows = df_raw.iloc[[0, 1]] # 상단 2줄 (헤더)
                        
                        # result_df의 인덱스는 df_analysis 기준 (0부터 시작)
                        # df_raw에서는 상단 2줄이 헤더이므로, 실제 데이터는 index + 2 위치에 있음
                        target_indices = result_df.index + 2
                        data_rows = df_raw.loc[target_indices]
                        
                        # 헤더와 필터링된 데이터 합치기
                        final_export_df = pd.concat([header_rows, data_rows])
                        
                        # 엑셀 다운로드 버퍼 생성
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            # 헤더 없이 씀 (이미 데이터프레임 안에 헤더가 포함되어 있으므로)
                            final_export_df.to_excel(writer, index=False, header=False)
                            
                        st.download_button(
                            label="📥 결과 엑셀 파일 다운로드 (원본 양식 유지)",
                            data=buffer.getvalue(),
                            file_name=f"filtered_result.xlsx",
                            mime="application/vnd.ms-excel"
                        )
                    else:
                        st.warning("조건에 맞는 데이터가 없습니다.")
                        
                except Exception as e:
                    st.error(f"코드 실행 중 오류가 발생했습니다: {e}")
                    st.error("쿼리를 조금 더 구체적으로 작성해보세요.")

            st.divider()
            with st.expander("🛠️ 사용된 프롬프트 확인하기"):
                st.text_area("GPT에게 전송된 프롬프트 내용:", value=used_prompt, height=300)

    except Exception as e:
        st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}")
        st.info("엑셀 파일 형식이 올바른지 확인해주세요.")