import streamlit as st
import pandas as pd
import openai
import io
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import platform

# --- 한글 폰트 설정 (Mac/Windows/Linux 환경 대응) ---
def set_korean_font():
    system_name = platform.system()
    if system_name == "Darwin": # Mac
        plt.rc('font', family='AppleGothic')
    elif system_name == "Windows": # Windows
        plt.rc('font', family='Malgun Gothic')
    else: # Linux (Streamlit Cloud 등)
        # 나눔고딕 등이 설치되어 있다고 가정하거나 기본 폰트 사용
        # Streamlit Cloud에서는 별도 폰트 설치가 필요할 수 있음
        plt.rc('font', family='DejaVu Sans') 
    plt.rc('axes', unicode_minus=False)

set_korean_font()

# 페이지 설정
st.set_page_config(page_title="엑셀 자연어 분석기", layout="wide")

st.title("📊 AI 기반 엑셀 데이터 분석 및 시각화 도구")
st.markdown("""
    업로드한 엑셀 파일(2단 헤더 구조 포함)을 자연어로 검색하거나 시각화할 수 있습니다.
    * **필터링:** "납품업체가 포스코인 것만 찾아줘"
    * **시각화:** "탄소 함량 분포를 히스토그램으로 보여줘"
""")

# --- 1. 사이드바: 설정 ---
with st.sidebar:
    st.header("설정 (Settings)")
    
    # API Key 처리 로직
    api_key = None
    if "OPENAI_API_KEY" in st.secrets:
        api_key = st.secrets["OPENAI_API_KEY"]
        st.success("✅ 저장된 API Key를 사용합니다.")
    else:
        api_key = st.text_input("OpenAI API Key", type="password", help="sk-로 시작하는 키를 입력하세요.")
    
    if not api_key:
        st.warning("API Key가 필요합니다.")
        st.stop()
    
    client = openai.OpenAI(api_key=api_key)

# --- 2. 메인: 파일 업로드 ---
uploaded_file = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])

def preprocess_multicolumn_header(df):
    """
    2단 헤더(MultiIndex)를 처리하여 읽기 쉬운 단일 컬럼명으로 변환합니다.
    """
    new_columns = []
    current_group = None
    
    for col in df.columns:
        group = str(col[0])
        item = str(col[1])
        
        if "Unnamed" in group or group == "nan":
            final_group = current_group
        else:
            current_group = group
            final_group = group
            
        if "Unnamed" in item or item == "nan":
            final_item = ""
        else:
            final_item = item
            
        if final_group and final_item:
            new_columns.append(f"{final_group}_{final_item}")
        elif final_group:
            new_columns.append(final_group)
        else:
            new_columns.append(final_item)
            
    df.columns = new_columns
    return df

def generate_df_summary(df):
    """
    LLM에게 데이터의 맥락을 제공하기 위해 컬럼별 데이터 타입과 샘플 값을 추출합니다.
    """
    summary = []
    for col in df.columns:
        dtype = df[col].dtype
        # 문자열(object)인 경우 고유값 상위 5개를 보여주어 매핑을 돕습니다.
        if dtype == 'object':
            unique_samples = df[col].dropna().unique()[:5]
            sample_str = ", ".join(map(str, unique_samples))
            summary.append(f"- Column: '{col}' (Type: String, Samples: [{sample_str}, ...])")
        # 숫자인 경우 범위 정보를 제공
        else:
            min_val = df[col].min()
            max_val = df[col].max()
            summary.append(f"- Column: '{col}' (Type: Number, Range: {min_val} ~ {max_val})")
    return "\n".join(summary)

def get_analysis_code(df, user_query):
    """
    OpenAI API를 사용하여 자연어를 판다스 필터링 또는 시각화 코드로 변환
    """
    data_context = generate_df_summary(df)
    
    prompt = f"""
    You are a Python Data Analyst using Streamlit.
    I have a pandas DataFrame named `df`.
    
    ### Data Context (Columns and Samples)
    {data_context}
    
    ### User Query
    "{user_query}"
    
    ### Instructions
    1. **Analyze Intent**: Determine if the user wants to **FILTER** data or **VISUALIZE** data.
    
    2. **Context-Aware Logic**: 
       - Look at the 'Samples' in the Data Context. 
       - If the user query mentions a value (e.g., "Posco"), but the sample shows a formal name (e.g., "(Corp) Posco"), use string matching (e.g., `str.contains`).
       - Do not assume exact matches for string columns.
    
    3. **Code Generation Rules**:
       - **IF FILTERING**: 
         - Create a new DataFrame named `result_df` containing the filtered data.
         - Do NOT create any charts.
       
       - **IF VISUALIZATION**:
         - Create a matplotlib figure `fig`.
         - Plot the data on `fig`.
         - **CRITICAL**: Use `st.pyplot(fig)` to display it.
         - Do NOT create `result_df`. Set `result_df = None`.
         - Use Korean fonts if needed, but rely on the environment settings provided.
         
    4. **Output Format**:
       - Output ONLY the Python code. No markdown, no explanations.
       - Assume `df`, `pd`, `plt`, `st` are already imported.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant specialized in pandas and streamlit."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        code = response.choices[0].message.content.strip().replace("```python", "").replace("```", "")
        return code, prompt
    except Exception as e:
        return str(e), prompt

if uploaded_file:
    # --- 3. 데이터 로드 전략 ---
    try:
        df_analysis = pd.read_excel(uploaded_file, header=[0, 1])
        df_analysis = preprocess_multicolumn_header(df_analysis)
        
        # 원본 구조 유지를 위한 Raw 데이터 로드
        uploaded_file.seek(0)
        df_raw = pd.read_excel(uploaded_file, header=None)
        
        with st.expander("📂 데이터 미리보기 및 컬럼 정보", expanded=True):
            st.dataframe(df_analysis.head())

        # --- 4. 쿼리 입력 ---
        st.divider()
        col_q1, col_q2 = st.columns([3, 1])
        with col_q1:
            user_query = st.text_input("🔍 질문을 입력하세요", 
                                     placeholder="예: 납품업체가 포스코인 것만 보여줘 또는 성분 C의 분포를 그려줘")
        
        with col_q2:
            run_btn = st.button("실행 (Analyze)", type="primary", use_container_width=True)

        # --- 5. 결과 처리 ---
        if run_btn and user_query:
            with st.spinner("AI가 데이터를 분석하고 코드를 작성 중입니다..."):
                # 1) 코드 생성
                generated_code, used_prompt = get_analysis_code(df_analysis, user_query)
                
                # 2) 코드 실행 환경 설정
                local_vars = {
                    'df': df_analysis, 
                    'pd': pd, 
                    'plt': plt, 
                    'st': st,
                    'result_df': None # 초기화
                }
                
                try:
                    # 실행
                    exec(generated_code, local_vars)
                    result_df = local_vars.get('result_df')
                    
                    # 3-A) 필터링 결과 처리
                    if result_df is not None and isinstance(result_df, pd.DataFrame):
                        if not result_df.empty:
                            st.success(f"✅ 검색 완료! {len(result_df)}개의 결과를 찾았습니다.")
                            st.dataframe(result_df)
                            
                            # 다운로드 로직 (원본 매핑)
                            header_rows = df_raw.iloc[[0, 1]] 
                            target_indices = result_df.index + 2
                            data_rows = df_raw.loc[target_indices]
                            final_export_df = pd.concat([header_rows, data_rows])
                            
                            buffer = io.BytesIO()
                            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                                final_export_df.to_excel(writer, index=False, header=False)
                                
                            st.download_button(
                                label="📥 엑셀로 다운로드 (원본 양식)",
                                data=buffer.getvalue(),
                                file_name="filtered_result.xlsx",
                                mime="application/vnd.ms-excel"
                            )
                        else:
                            st.warning("조건에 맞는 데이터가 없습니다.")
                    
                    # 3-B) 시각화 결과 처리 (result_df가 None인 경우)
                    elif result_df is None:
                        # exec 내부에서 st.pyplot()이 실행되었을 것임
                        st.success("✅ 시각화 완료")
                    
                    else:
                        st.info("결과를 표시할 수 없습니다. (코드 실행은 완료됨)")

                except Exception as e:
                    st.error(f"코드 실행 중 오류가 발생했습니다: {e}")
                    with st.expander("에러 상세 정보"):
                        st.code(generated_code)

            # --- 디버깅용 (선택) ---
            with st.expander("🛠️ 내부 프롬프트 및 생성 코드 확인"):
                st.write("**생성된 코드:**")
                st.code(generated_code, language='python')
                st.write("**사용된 프롬프트:**")
                st.text(used_prompt)

    except Exception as e:
        st.error(f"파일 처리 중 오류: {e}")