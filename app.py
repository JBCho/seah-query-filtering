import streamlit as st
import pandas as pd
import openai
import io
import re

# 페이지 설정
st.set_page_config(page_title="엑셀 자연어 분석기", layout="wide")

st.title("📊 AI 기반 엑셀 데이터 분석 및 추출기")
st.markdown("""
    업로드한 엑셀 파일(2단 헤더 구조 포함)을 자연어로 검색하고, 
    **원본 양식을 유지한 채** 결과를 다운로드할 수 있습니다.
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
        st.warning("API Key가 필요합니다. .streamlit/secrets.toml에 설정하거나 입력해주세요.")
        st.stop()
    
    client = openai.OpenAI(api_key=api_key)

# --- 2. 메인: 파일 업로드 ---
uploaded_file = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])

def preprocess_multicolumn_header(df):
    """
    2단 헤더(MultiIndex)를 처리하여 읽기 쉬운 단일 컬럼명으로 변환합니다.
    예: ('성분 시험 횟수 1', 'C') -> '성분 시험 횟수 1_C'
    """
    new_columns = []
    current_group = None
    
    # df.columns가 MultiIndex라고 가정하고 순회
    for col in df.columns:
        # col은 (Level0, Level1) 형태의 튜플
        group = str(col[0])
        item = str(col[1])
        
        # 1) 그룹(첫 번째 행) 처리: Unnamed나 nan이면 이전 그룹 유지 (Forward Fill)
        # 단, 파일의 맨 처음 컬럼들이 그룹 없이 시작하는 경우는 유지
        if "Unnamed" in group or group == "nan":
            final_group = current_group
        else:
            current_group = group
            final_group = group
            
        # 2) 항목(두 번째 행) 처리: Unnamed나 nan이면 빈 문자열
        if "Unnamed" in item or item == "nan":
            final_item = ""
        else:
            final_item = item
            
        # 3) 최종 병합
        if final_group and final_item:
            # 그룹과 항목이 둘 다 있으면 "그룹_항목" (예: 성분 시험 횟수 1_C)
            new_columns.append(f"{final_group}_{final_item}")
        elif final_group:
            # 항목이 없으면 그룹만 (드문 경우)
            new_columns.append(final_group)
        else:
            # 그룹이 없으면 항목만 (예: 시편배치, Heat No.)
            new_columns.append(final_item)
            
    df.columns = new_columns
    return df

def get_filter_code(df_columns, user_query):
    """
    OpenAI API를 사용하여 자연어를 판다스 필터링 코드로 변환
    """
    columns_list = list(df_columns)
    
    prompt = f"""
    You are a Python Data Analyst.
    I have a pandas DataFrame named `df`.
    The columns have been pre-processed to combine header categories using underscores.
    
    Columns: {columns_list}
    
    User Query: "{user_query}"
    
    Task:
    1. Generate a Python code snippet to filter `df` based on the query.
    2. Use the exact column names provided above. For example, use '성분 시험 횟수 1_C' instead of 'C'.
    3. Store the result in a variable named `result_df`.
    4. Output ONLY the python code. No markdown, no explanations.
    
    Example:
    Query: "1차 성분 시험에서 C가 0.05 이상인 것"
    Code: result_df = df[df['성분 시험 횟수 1_C'] > 0.05]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o", # 복잡한 컬럼 추론을 위해 gpt-4o 권장
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant."},
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
        # [수정됨] 분석용 데이터: header=[0, 1]로 읽어서 2줄을 모두 가져옴
        df_analysis = pd.read_excel(uploaded_file, header=[0, 1])
        
        # [수정됨] 컬럼명 전처리 실행 (MultiIndex -> Flat Index)
        df_analysis = preprocess_multicolumn_header(df_analysis)
        
        # 원본 구조 유지를 위한 Raw 데이터 로드 (헤더 없이 로드)
        uploaded_file.seek(0)
        df_raw = pd.read_excel(uploaded_file, header=None)
        
        # 미리보기
        with st.expander("📂 업로드된 파일 미리보기 (첫 5행)", expanded=True):
            st.dataframe(df_analysis.head())
            st.caption(f"총 {len(df_analysis)}개의 행이 감지되었습니다. 상단 컬럼명이 '대분류_항목' 형태로 자동 변환되었습니다.")

        # --- 4. 쿼리 입력 ---
        st.divider()
        col_q1, col_q2 = st.columns([3, 1])
        with col_q1:
            user_query = st.text_input("🔍 질문을 입력하세요", 
                                     placeholder="예: '탄소 함량이 0.05 이상이고 T방향 연신율이 50 미만인 걸 찾아줘'")
        
        with col_q2:
            run_btn = st.button("검색 실행", type="primary", use_container_width=True)

        # --- 5. 결과 처리 ---
        if run_btn and user_query:
            with st.spinner("AI가 컬럼 구조를 이해하고 데이터를 분석 중입니다..."):
                # 1) 코드 생성
                generated_code, used_prompt = get_filter_code(df_analysis.columns, user_query)
                
                # 2) 코드 실행
                local_vars = {'df': df_analysis}
                try:
                    exec(generated_code, {}, local_vars)
                    result_df = local_vars.get('result_df')
                    
                    if result_df is not None and not result_df.empty:
                        st.success(f"검색 완료! {len(result_df)}개의 결과를 찾았습니다.")
                        
                        # --- 6. 결과 조회 및 다운로드 ---
                        st.dataframe(result_df)
                        
                        # [다운로드 로직]
                        # df_analysis는 header=[0, 1]로 읽었으므로 인덱스 0은 실제 데이터 1행임.
                        # df_raw는 header=None으로 읽었으므로, 인덱스 0, 1은 헤더, 인덱스 2부터 데이터임.
                        # 따라서 df_analysis의 index + 2 가 df_raw의 해당 데이터 위치임.
                        
                        header_rows = df_raw.iloc[[0, 1]] # 상단 2줄 (헤더)
                        target_indices = result_df.index + 2
                        data_rows = df_raw.loc[target_indices]
                        
                        final_export_df = pd.concat([header_rows, data_rows])
                        
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
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
                    with st.expander("에러 상세 정보"):
                        st.write(generated_code)

            # --- 7. 프롬프트 확인 (토글) ---
            st.divider()
            with st.expander("🛠️ 사용된 프롬프트 확인하기"):
                st.text_area("GPT에게 전송된 프롬프트 내용:", value=used_prompt, height=300)

    except Exception as e:
        st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}")
        st.info("이 파일이 2단 헤더 구조가 맞는지 확인해주세요. (일반 엑셀 파일은 에러가 날 수 있습니다)")