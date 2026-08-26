#!/usr/bin/env python3
"""1000개 질문 배치 테스트 → Phoenix 추적 확인.

사전 조건:
  Phoenix 실행 중: .venv/bin/phoenix serve
  API 키 설정:    .streamlit/secrets.toml

실행:
  .venv/bin/python batch_test.py
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

# ── Phoenix / OTEL 초기화 ────────────────────────────────────────────
from phoenix.otel import register as _register

_register(
    project_name="batch-test-1000",
    endpoint="http://localhost:6006/v1/traces",
    batch=True,
    verbose=False,
)

# OpenAI 자동 계측 (키가 없어도 등록 자체는 무해)
try:
    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument()
except Exception:
    pass

import llm  # noqa: E402  (OTEL 등록 후 import 해야 계측 적용)

# ── 설정 ─────────────────────────────────────────────────────────────
SECRETS = Path(".streamlit/secrets.toml")
RESULTS_FILE = Path("batch_test_results.json")
MAX_WORKERS = 20     # 동시 요청 수 (OpenAI: 500 RPM 기준)
# 채점(GRADE)이 켜지면 문항당 호출이 답변+채점 2회로 늘어 실질 요청량이 두 배가 된다.
# 20 그대로 두면 999문항 기준 약 800 RPM 이 나와 429(Rate limit)를 맞는다 —
# 2026-08-26 실행에서 응답 6건 실패 + 채점 183건 누락이 전부 이 원인이었다.
GRADE_WORKER_DIVISOR = 2
MAX_RETRIES = 4      # 429 재시도 횟수
SYSTEM_PROMPT = "한 문장으로 간결하게 답하세요."


# ── API 키 / 제공자 자동 감지 ────────────────────────────────────────
def _load_provider() -> tuple[str, str, str]:
    if not SECRETS.exists():
        sys.exit("secrets.toml 을 찾을 수 없습니다.")
    with open(SECRETS, "rb") as f:
        s = tomllib.load(f)
    for provider, model_default in [("OpenAI", "gpt-4o-mini"), ("Gemini", "gemini-2.0-flash")]:  # OpenAI 우선
        key_name = llm.secret_key_for(provider)
        val = s.get(key_name, "").strip()
        if val and "여기에" not in val:
            return provider, model_default, val
    sys.exit("유효한 API 키가 없습니다. secrets.toml 을 확인하세요.")


# ── 1000개 질문 생성 ─────────────────────────────────────────────────
def _make_questions() -> list[dict]:
    qs: list[dict] = []

    def add(category: str, questions: list[str], expecteds: list | None = None):
        for i, q in enumerate(questions):
            item = {"category": category, "question": q,
                    "expected": expecteds[i] if expecteds is not None else None}
            qs.append(item)

    # 1. 수학 (100) — 결정적 정답을 함께 저장(exact 채점)
    math, math_ans = [], []
    ops = [("+", lambda a, b: a + b), ("×", lambda a, b: a * b),
           ("-", lambda a, b: a - b), ("÷", lambda a, b: a // b)]
    for op_sym, fn in ops:
        for _ in range(25):
            a = random.randint(2, 50)
            b = random.randint(2, 50) if op_sym != "÷" else random.randint(2, 10)
            if op_sym == "÷":
                a = a * b
            math.append(f"{a} {op_sym} {b} = ?")
            math_ans.append(str(fn(a, b)))
    add("수학", math, math_ans)

    # 2. 파이썬 코딩 (100)
    coding_base = [
        "파이썬에서 리스트의 마지막 요소를 가져오는 방법은?",
        "파이썬 딕셔너리에서 키 존재 여부를 확인하는 방법은?",
        "파이썬에서 파일을 읽는 올바른 방법은?",
        "파이썬 리스트 컴프리헨션 예시를 간단히 보여주세요.",
        "파이썬에서 None 을 확인하는 올바른 방법은?",
        "파이썬 lambda 함수의 예시를 보여주세요.",
        "파이썬에서 try-except 기본 구조는?",
        "파이썬 enumerate() 의 용도는?",
        "파이썬에서 두 리스트를 합치는 방법은?",
        "파이썬 f-string 사용 예시를 보여주세요.",
        "파이썬 @staticmethod 와 @classmethod 의 차이는?",
        "파이썬에서 *args 와 **kwargs 의 차이는?",
        "파이썬 generator 와 iterator 의 차이는?",
        "파이썬 set 과 frozenset 의 차이는?",
        "파이썬에서 deepcopy 가 필요한 경우는?",
        "파이썬 with 문(컨텍스트 매니저)의 목적은?",
        "파이썬에서 모듈을 임포트하는 두 가지 방법은?",
        "파이썬 zip() 함수의 용도는?",
        "파이썬 sorted() 와 list.sort() 의 차이는?",
        "파이썬에서 문자열을 정수로 변환하는 방법은?",
    ]
    coding = []
    for i, q in enumerate(coding_base * 5):
        coding.append(f"[Q{i+1}] {q}")
    add("파이썬코딩", coding[:100])

    # 3. 과학 (100)
    science = [
        "빛의 속도는 초속 몇 km인가?", "물의 화학식은?", "태양계에서 가장 큰 행성은?",
        "원자핵을 구성하는 두 입자는?", "광합성 반응에 필요한 물질 두 가지는?",
        "DNA 이중나선 구조를 발견한 과학자는?", "원소 주기율표를 만든 사람은?",
        "소리의 속도는 공기 중에서 약 몇 m/s인가?", "지구의 자전 주기는?",
        "세포의 에너지 화폐라 불리는 분자는?", "전기저항의 단위는?",
        "뉴턴의 제1법칙은?", "혈액의 pH 범위는?", "인체에서 가장 큰 기관은?",
        "태양까지의 평균 거리는 약 몇 km인가?", "H2O에서 수소와 산소의 비율은?",
        "절대영도는 섭씨 몇 도인가?", "산성비의 pH 기준은?",
        "대기의 주성분(약 78%)은?", "뉴턴의 만유인력 법칙은?",
        "빛이 물에서 굴절되는 현상의 이름은?", "인체에서 가장 강한 근육은?",
        "달의 평균 지구 거리는 약 몇 km인가?", "핵분열과 핵융합의 차이는?",
        "탄소 원자번호는?", "수소 원자번호는?", "산소 원자번호는?",
        "혈액형을 결정하는 항원은?", "바이러스와 세균의 차이는?",
        "진화론을 제안한 과학자는?", "쿼크는 무엇인가?", "플라즈마 상태란?",
        "반도체란?", "레이저의 원리는?", "MRI 원리는?",
        "뇌의 좌뇌와 우뇌의 차이는?", "인체 뼈의 개수는?",
        "지구 대기권 층의 순서는?", "오존층은 어느 대기층에 있나?",
        "엽록소가 흡수하는 빛의 색은?", "멘델의 유전 법칙은?",
        "세포분열의 두 종류는?", "신경세포를 뉴런이라 부를 때 구성 요소는?",
        "혈액 속 백혈구의 역할은?", "항생제가 작용하지 않는 병원체는?",
        "광년(light-year)이란?", "블랙홀이란?", "초신성이란?",
        "주기율표에서 금속과 비금속의 경계는?",
        "전자기파의 종류를 파장 순으로 나열하면?",
        "화석 연료 연소 시 주로 발생하는 온실기체는?",
        "베르누이 원리는?", "열역학 제2법칙은?", "엔트로피란?",
        "양자역학의 불확정성 원리는?", "파동-입자 이중성이란?",
        "도체와 절연체의 차이는?", "자기장이란?", "전류의 단위는?",
        "전압의 단위는?", "전력의 공식은?", "옴의 법칙은?",
        "산화와 환원이란?", "촉매의 역할은?", "이온결합과 공유결합의 차이는?",
        "산과 염기의 정의는?", "pH 7은 중성, 7 미만은?", "동위원소란?",
        "방사성 붕괴란?", "반감기란?", "핵에너지와 화학에너지의 차이는?",
        "세포막의 주요 구성 성분은?", "미토콘드리아의 역할은?",
        "리보솜의 역할은?", "효소란?", "면역계의 T세포 역할은?",
        "인슐린의 역할은?", "DNA와 RNA의 차이는?", "단백질 합성 과정은?",
        "생태계에서 생산자·소비자·분해자의 역할은?",
        "먹이사슬과 먹이그물의 차이는?", "지구온난화 원인은?",
        "재생에너지 종류 3가지는?", "핵발전소 원리는?",
        "나노기술이란?", "인공지능과 머신러닝의 차이는?",
        "트랜지스터의 역할은?", "CPU와 GPU의 차이는?",
        "양자컴퓨터의 원리는?", "CRISPR 기술이란?",
        "줄기세포란?", "백신의 작용 원리는?", "항체란?",
        "신진대사란?", "체온 항상성 유지 기관은?",
        "운동 후 근육통의 원인은?", "뇌졸중과 심근경색의 차이는?",
        "유전자와 염색체의 관계는?", "23쌍의 염색체 중 성염색체는?",
    ]
    add("과학", science[:100])

    # 4. 한국 역사 (100)
    history_kr = [
        "고조선을 세운 단군은 언제 건국했나?", "삼국시대 세 나라는?",
        "신라가 삼국을 통일한 연도는?", "고려를 건국한 사람은?",
        "고려의 수도는?", "조선을 건국한 사람은?", "조선의 수도는?",
        "한글을 창제한 왕은?", "한글 창제 연도는?", "임진왜란 발발 연도는?",
        "이순신 장군이 이긴 해전 중 가장 유명한 것은?", "병자호란 발발 연도는?",
        "조선 후기 실학의 대표 학자는?", "갑신정변이 일어난 해는?",
        "동학농민운동이 일어난 해는?", "대한제국 선포 연도는?",
        "경술국치(한일병합)가 일어난 해는?", "3·1 운동이 일어난 해는?",
        "대한민국 임시정부가 세워진 도시는?", "광복절은 몇 월 며칠인가?",
        "대한민국 정부 수립 연도는?", "6·25 전쟁 발발 연도는?",
        "4·19 혁명이 일어난 해는?", "5·16 군사정변이 일어난 해는?",
        "박정희 대통령 재임 시작 연도는?", "새마을운동 시작 연도는?",
        "10·26 사태가 일어난 해는?", "5·18 광주민주화운동이 일어난 해는?",
        "서울 올림픽 개최 연도는?", "IMF 외환위기가 일어난 해는?",
        "남북정상회담 최초 개최 연도는?", "2002 한일 월드컵 한국 성적은?",
        "세종대왕의 업적 중 과학 기구는?", "고려청자의 특징은?",
        "조선백자의 특징은?", "훈민정음이란?", "고려대장경이란?",
        "팔만대장경이 보관된 곳은?", "석굴암이 있는 도시는?",
        "불국사가 있는 도시는?", "경복궁이 있는 도시는?",
        "창덕궁이 유네스코 세계문화유산인 이유는?",
        "판소리의 다섯 마당은?", "탈춤의 대표적 장르는?",
        "거북선을 만든 사람은?", "신사임당의 업적은?",
        "퇴계 이황과 율곡 이이의 공통점은?", "정약용의 대표 저서는?",
        "김구 선생의 별명은?", "안중근 의사가 의거를 일으킨 도시는?",
        "윤봉길 의사 의거 장소는?", "유관순 열사 출신지는?",
        "신채호의 대표 저서는?", "박은식의 대표 저서는?",
        "독립신문을 창간한 사람은?", "한성순보는 어떤 신문인가?",
        "최초의 한국 근대 소설은?", "이광수의 대표 소설은?",
        "김소월의 대표 시는?", "윤동주의 대표 시는?",
        "아리랑의 의미는?", "판소리 춘향가의 줄거리는?",
        "삼강오륜이란?", "성리학을 조선에 도입한 학자는?",
        "동의보감을 저술한 사람은?", "혼천의란?",
        "앙부일구란?", "측우기를 만든 왕은?",
        "칠정산이란?", "대동여지도를 만든 사람은?",
        "거중기를 발명한 사람은?", "수원 화성을 건축한 왕은?",
        "노비제도가 공식 폐지된 해는?", "갑오개혁이 일어난 해는?",
        "독도가 한국 영토임을 명시한 역사 자료는?",
        "신라 골품제란?", "고려 과거제 도입 시기는?",
        "조선 과거 시험의 종류는?", "성균관이란?",
        "향교란?", "서원이란?", "규장각이란?",
        "비변사란?", "삼정의 문란이란?",
        "임오군란이 일어난 해는?", "갑오경장이란?",
        "을미사변이란?", "아관파천이란?",
        "간도 협약이란?", "을사늑약이란?",
        "의병운동이 일어난 시기는?", "신민회란?",
        "물산장려운동이란?", "6·10 만세운동이란?",
        "광주학생항일운동이란?", "한인애국단이란?",
        "조선어학회 사건이란?", "여운형의 건국준비위원회란?",
        "반민족행위처벌법이란?", "농지개혁법이란?",
        "사사오입 개헌이란?", "발췌 개헌이란?",
    ]
    add("한국역사", history_kr[:100])

    # 5. 세계 지리 — 수도 (100)
    capitals = [
        ("프랑스", "파리"), ("독일", "베를린"), ("영국", "런던"),
        ("미국", "워싱턴 D.C."), ("일본", "도쿄"), ("중국", "베이징"),
        ("러시아", "모스크바"), ("캐나다", "오타와"), ("호주", "캔버라"),
        ("브라질", "브라질리아"), ("아르헨티나", "부에노스아이레스"),
        ("멕시코", "멕시코시티"), ("이탈리아", "로마"), ("스페인", "마드리드"),
        ("포르투갈", "리스본"), ("네덜란드", "암스테르담"), ("벨기에", "브뤼셀"),
        ("스위스", "베른"), ("오스트리아", "빈"), ("스웨덴", "스톡홀름"),
        ("노르웨이", "오슬로"), ("덴마크", "코펜하겐"), ("핀란드", "헬싱키"),
        ("폴란드", "바르샤바"), ("체코", "프라하"), ("헝가리", "부다페스트"),
        ("그리스", "아테네"), ("터키", "앙카라"), ("이집트", "카이로"),
        ("남아프리카공화국", "프리토리아"), ("나이지리아", "아부자"),
        ("에티오피아", "아디스아바바"), ("케냐", "나이로비"),
        ("인도", "뉴델리"), ("파키스탄", "이슬라마바드"),
        ("방글라데시", "다카"), ("인도네시아", "자카르타"),
        ("말레이시아", "쿠알라룸푸르"), ("태국", "방콕"),
        ("베트남", "하노이"), ("필리핀", "마닐라"),
        ("싱가포르", "싱가포르"), ("미얀마", "네피도"),
        ("사우디아라비아", "리야드"), ("이란", "테헤란"),
        ("이라크", "바그다드"), ("이스라엘", "예루살렘"),
        ("아랍에미리트", "아부다비"), ("카자흐스탄", "아스타나"),
        ("우크라이나", "키이우"), ("루마니아", "부쿠레슈티"),
        ("불가리아", "소피아"), ("세르비아", "베오그라드"),
        ("크로아티아", "자그레브"), ("슬로베니아", "류블랴나"),
        ("슬로바키아", "브라티슬라바"), ("리투아니아", "빌뉴스"),
        ("라트비아", "리가"), ("에스토니아", "탈린"),
        ("아이슬란드", "레이캬비크"), ("아일랜드", "더블린"),
        ("뉴질랜드", "웰링턴"), ("페루", "리마"),
        ("콜롬비아", "보고타"), ("칠레", "산티아고"),
        ("베네수엘라", "카라카스"), ("에콰도르", "키토"),
        ("볼리비아", "수크레"), ("파라과이", "아순시온"),
        ("우루과이", "몬테비데오"), ("쿠바", "아바나"),
        ("자메이카", "킹스턴"), ("아이티", "포르토프랭스"),
        ("모로코", "라바트"), ("알제리", "알제"),
        ("튀니지", "튀니스"), ("리비아", "트리폴리"),
        ("수단", "하르툼"), ("가나", "아크라"),
        ("세네갈", "다카르"), ("카메룬", "야운데"),
        ("콩고민주공화국", "킨샤사"), ("탄자니아", "도도마"),
        ("모잠비크", "마푸투"), ("마다가스카르", "안타나나리보"),
        ("앙골라", "루안다"), ("잠비아", "루사카"),
        ("짐바브웨", "하라레"), ("보츠와나", "가보로네"),
        ("나미비아", "빈트후크"), ("우간다", "캄팔라"),
        ("르완다", "키갈리"), ("쿠웨이트", "쿠웨이트시티"),
        ("카타르", "도하"), ("바레인", "마나마"),
        ("오만", "무스카트"), ("요르단", "암만"),
        ("레바논", "베이루트"), ("시리아", "다마스쿠스"),
        ("아프가니스탄", "카불"), ("네팔", "카트만두"),
        ("스리랑카", "스리자야와르데네푸라코테"), ("몰디브", "말레"),
        ("몽골", "울란바토르"), ("북한", "평양"),
    ]
    add("세계지리", [f"{c}의 수도는?" for c, _ in capitals[:100]])

    # 6. 영어 (100)
    english = [
        "apple의 한국어 뜻은?", "beautiful의 반의어는?", "run의 과거형은?",
        "happy의 부사형은?", "big의 비교급은?", "go의 과거분사는?",
        "I am going to school. 이 문장의 시제는?",
        "She has lived here for 10 years. 이 문장의 시제는?",
        "관계대명사 who와 which의 차이는?",
        "수동태 문장으로 바꾸기: Tom ate the apple.",
        "감탄문으로 바꾸기: She is very beautiful.",
        "book의 복수형은?", "child의 복수형은?", "mouse의 복수형은?",
        "good의 최상급은?", "bad의 비교급은?", "far의 최상급은?",
        "I, my, me, mine의 차이는?", "형용사와 부사의 차이는?",
        "현재완료와 과거 시제의 차이는?",
    ] * 5
    add("영어", english[:100])

    # 7. 논리/수수께끼 (100)
    logic_base = [
        "아버지의 딸의 아버지는 나에게 누구인가?",
        "닭이 먼저인가, 달걀이 먼저인가? 진화론적 관점에서 답하면?",
        "방에 세 개의 전구가 있다. 세 개의 스위치 중 하나로 켤 수 있는 전구를 찾으려면 최소 몇 번 들어가야 하나?",
        "거짓말쟁이 마을과 진실쟁이 마을이 있다. 만난 사람에게 '당신은 어느 마을 출신?'이라고 물으면 뭐라 답하나?",
        "1부터 100까지 더하면?",
        "소수(prime number)가 아닌 것은? 2, 3, 4, 5",
        "삼각형의 내각의 합은?",
        "정육면체의 면 개수는?",
        "두 사람이 동전을 공정하게 나누는 방법은?",
        "A가 B보다 크고 B가 C보다 크면, A와 C의 관계는?",
        "5분짜리 초 2개로 정확히 7.5분을 재는 방법은?",
        "강 건너기 문제: 여우, 닭, 쌀을 혼자 배로 옮길 때 순서는?",
        "두 개의 달걀로 100층 빌딩에서 깨지지 않는 최고층을 찾으려면?",
        "0으로 나눌 수 없는 이유는?",
        "피타고라스 정리는?",
        "원의 넓이 공식은?",
        "구의 부피 공식은?",
        "로그와 지수의 관계는?",
        "집합에서 합집합과 교집합의 차이는?",
        "확률 0.5란 어떤 의미인가?",
    ]
    logic = []
    for i, q in enumerate(logic_base * 5):
        logic.append(f"[논리{i+1}] {q}")
    add("논리", logic[:100])

    # 8. 경제 (100)
    economy = [
        "GDP란 무엇인가?", "GNP와 GDP의 차이는?", "인플레이션이란?",
        "디플레이션이란?", "스태그플레이션이란?", "기준금리란?",
        "양적완화(QE)란?", "재정정책과 통화정책의 차이는?",
        "공급과 수요의 법칙은?", "가격 탄력성이란?",
        "비교우위란?", "자유무역과 보호무역의 차이는?",
        "관세란?", "환율이란?", "환율이 오르면 수출에 어떤 영향?",
        "주식과 채권의 차이는?", "배당이란?", "PER이란?",
        "ROE란?", "부채비율이란?",
        "기회비용이란?", "매몰비용이란?", "한계비용이란?",
        "외부효과(externality)란?", "공공재란?",
        "독점과 과점의 차이는?", "카르텔이란?",
        "최저임금제의 장단점은?", "기본소득이란?",
        "복지국가란?", "케인즈 경제학이란?",
        "공급주의 경제학이란?", "마르크스 경제학이란?",
        "시장실패란?", "정보 비대칭이란?",
        "역선택(adverse selection)이란?", "도덕적 해이란?",
        "경기침체와 불황의 차이는?", "경기 사이클이란?",
        "선행지표와 후행지표의 차이는?", "소비자물가지수(CPI)란?",
        "생산자물가지수(PPI)란?", "실업률 계산 방법은?",
        "경상수지란?", "자본수지란?", "외환보유고란?",
        "국가부채란?", "재정적자란?", "재정흑자란?",
        "세금의 종류(직접세·간접세)는?", "부가가치세란?",
    ] * 2
    add("경제", economy[:100])

    # 9. 상식 (100)
    general = [
        "올림픽은 몇 년마다 열리나?", "FIFA 월드컵은 몇 년마다 열리나?",
        "노벨상 시상식은 매년 몇 월에 열리나?", "UN 본부가 있는 도시는?",
        "WHO의 설립 연도는?", "IMF의 설립 목적은?", "WTO의 역할은?",
        "인터넷이 발명된 시기는?", "스마트폰을 처음 대중화시킨 제품은?",
        "구글이 설립된 연도는?", "아마존이 처음 판매한 상품은?",
        "애플의 공동창업자 3명은?", "마이크로소프트 창업자는?",
        "테슬라 CEO는?", "스페이스X 설립자는?",
        "셰익스피어의 4대 비극은?", "다빈치의 대표작은?",
        "베토벤의 대표 교향곡은?", "모차르트 출생 연도는?",
        "반 고흐의 대표작은?", "피카소의 화풍은?",
        "기네스북이란?", "에베레스트산 높이는?",
        "세계에서 가장 긴 강은?", "세계에서 가장 큰 바다는?",
        "세계에서 가장 큰 사막은?", "세계에서 가장 많이 쓰이는 언어는?",
        "세계에서 인구가 가장 많은 나라는?", "세계에서 면적이 가장 큰 나라는?",
        "빛이 없는 공간의 색은?", "무지개의 색 순서는?",
        "1킬로그램은 몇 그램?", "1마일은 몇 킬로미터?",
        "1인치는 몇 센티미터?", "물의 끓는점(1기압)은?",
        "물의 어는점(1기압)은?", "인체 체온 정상 범위는?",
        "사람의 평균 심박수는?", "사람의 혈압 정상값은?",
        "사람의 수명은 평균 몇 세인가?", "수면이 필요한 이유는?",
        "아침을 먹어야 하는 이유는?", "운동의 장점 3가지는?",
        "명상의 효과는?", "독서의 장점은?",
        "음악이 감정에 미치는 영향은?", "예술의 사회적 역할은?",
        "교육의 목적은?", "민주주의의 기본 원칙은?",
        "인권이란?", "법 앞에 평등이란?",
    ] * 2
    add("상식", general[:100])

    # 10. 문화/예술 (100)
    culture = [
        "한식 중 유네스코 인류무형유산은?", "K-pop이 세계적으로 인기 있는 이유는?",
        "BTS의 결성 연도는?", "블랙핑크 멤버는?",
        "영화 '기생충'의 감독은?", "영화 '기생충'이 오스카 수상한 해는?",
        "'오징어게임'이 넷플릭스 공개된 해는?", "한국 드라마의 특징은?",
        "클래식 음악의 시대 순서는?", "바로크 음악의 특징은?",
        "르네상스 미술의 특징은?", "인상주의 미술의 특징은?",
        "추상주의 미술의 시작은?", "팝아트의 대표 작가는?",
        "재즈의 발원지는?", "블루스의 특징은?",
        "힙합의 발원지는?", "록 음악의 기원은?",
        "오페라와 뮤지컬의 차이는?", "발레의 발원지는?",
        "헤밍웨이의 대표작은?", "도스토옙스키의 대표작은?",
        "톨스토이의 대표작은?", "카프카의 대표작은?",
        "가브리엘 가르시아 마르케스의 대표작은?",
        "마르셀 프루스트의 대표작은?", "조지 오웰의 대표작은?",
        "올더스 헉슬리의 대표작은?", "프란츠 카프카의 작풍은?",
        "일본 하이쿠란?", "중국 당시(唐詩)의 대표 시인은?",
        "이백과 두보의 공통점은?", "소동파의 업적은?",
        "서예의 종류는?", "도자기의 종류는?",
        "조각의 대표 기법은?", "판화란?",
        "사진예술의 역사는?", "영화의 역사는?",
        "루브르 박물관 소장품 중 가장 유명한 것은?",
        "뉴욕 현대미술관(MoMA) 대표 소장품은?",
        "건축의 고딕 양식 특징은?", "로마네스크 양식이란?",
        "바우하우스란?", "르 코르뷔지에의 건축 철학은?",
        "세계 7대 불가사의는?", "피라미드 건설 방법에 대한 학설은?",
        "만리장성의 길이는?", "타지마할이 건축된 이유는?",
        "콜로세움의 용도는?",
    ] * 2
    add("문화예술", culture[:100])

    random.shuffle(qs)
    return qs[:1000]


# ── 정답 채점 ────────────────────────────────────────────────────────
GRADE = True   # --no-grade 로 끌 수 있음
JUDGE_SYSTEM = (
    "너는 채점자다. 아래 [질문]과 [답변]을 보고 답변이 사실적으로 정확하면 'O', "
    "부정확하거나 틀리면 'X' 한 글자만 출력해라. 다른 말은 절대 하지 마라."
)


def _normalize(s: str) -> str:
    return re.sub(r"[\s,]", "", (s or "")).lower()


def _grade(item: dict, response: str, provider: str, model: str, api_key: str):
    """정답 여부 채점. 반환 (correct: bool|None, graded_by: 'exact'|'judge'|None)."""
    expected = item.get("expected")
    if expected is not None:  # 결정적 정답(수학 등): 응답의 숫자 토큰과 정확히 일치하는지
        nums = re.findall(r"-?\d+", _normalize(response))
        return (str(expected) in nums), "exact"
    try:  # LLM-as-judge
        out = "".join(llm.stream_chat(
            provider=provider, api_key=api_key, model=model,
            messages=[{"role": "user",
                       "content": f"[질문]\n{item['question']}\n\n[답변]\n{response}"}],
            temperature=0.0, system_prompt=JUDGE_SYSTEM,
        )).strip().upper()
        if "O" in out[:2]:
            return True, "judge"
        if "X" in out[:2]:
            return False, "judge"
        return None, "judge"
    except Exception:
        return None, "judge"


# ── 단일 질문 실행 (재시도 포함) ────────────────────────────────────
def run_one(idx: int, item: dict, provider: str, model: str, api_key: str) -> dict:
    t0 = time.time()
    for attempt in range(MAX_RETRIES):
        try:
            chunks = list(llm.stream_chat(
                provider=provider,
                api_key=api_key,
                model=model,
                messages=[{"role": "user", "content": item["question"]}],
                temperature=0.2,
                system_prompt=SYSTEM_PROMPT,
            ))
            response = "".join(chunks)
            latency_ms = round((time.time() - t0) * 1000)  # 채점 호출 전에 응답 지연 확정
            correct, graded_by = (_grade(item, response, provider, model, api_key)
                                  if GRADE else (None, None))
            return {
                "idx": idx,
                "category": item["category"],
                "question": item["question"],
                "response": response,
                "latency_ms": latency_ms,
                "model": model,
                "provider": provider,
                "success": True,
                "error": None,
                "correct": correct,
                "graded_by": graded_by,
            }
        except Exception as e:
            err = str(e)
            is_rate_limit = any(k in err.lower() for k in ("429", "rate", "quota", "exhausted"))
            if is_rate_limit and attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1) + random.uniform(0, 1))
                continue
            return {
                "idx": idx,
                "category": item["category"],
                "question": item["question"],
                "response": None,
                "latency_ms": round((time.time() - t0) * 1000),
                "model": model,
                "provider": provider,
                "success": False,
                "error": err[:200],
                "correct": False,
                "graded_by": None,
            }


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    global GRADE
    import argparse
    ap = argparse.ArgumentParser(description="AI은행원 배치 테스트(응답 성공률·지연 + 정답 채점)")
    ap.add_argument("--no-grade", action="store_true", help="정답 채점 생략(응답 성공률·지연만)")
    ap.add_argument("--limit", type=int, default=0, help="질문 수 제한(빠른 검증용)")
    ap.add_argument("--workers", type=int, default=0,
                    help=f"동시 요청 수 직접 지정(기본: 채점 ON 이면 {MAX_WORKERS // GRADE_WORKER_DIVISOR}, OFF 면 {MAX_WORKERS})")
    args = ap.parse_args()
    if args.no_grade:
        GRADE = False

    workers = args.workers if args.workers > 0 else (
        MAX_WORKERS // GRADE_WORKER_DIVISOR if GRADE else MAX_WORKERS)

    provider, model, api_key = _load_provider()
    questions = _make_questions()
    if args.limit and args.limit > 0:
        questions = questions[:args.limit]

    print(f"\n{'='*60}")
    print(f"  배치 테스트 시작")
    print(f"  제공자: {provider} / 모델: {model}")
    print(f"  질문 수: {len(questions)} / 동시 요청: {workers} / 채점: {'ON' if GRADE else 'OFF'}")
    print(f"  Phoenix: http://localhost:6006  (프로젝트: batch-test-1000)")
    print(f"{'='*60}\n")

    started_at = datetime.now().isoformat()
    results: list[dict] = [None] * len(questions)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_one, i, item, provider, model, api_key): i
            for i, item in enumerate(questions)
        }
        with tqdm(total=len(questions), unit="q", ncols=70) as bar:
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                r = results[idx]
                bar.set_postfix(
                    ok="✓" if r["success"] else "✗",
                    lat=f"{r['latency_ms']}ms",
                    refresh=False,
                )
                bar.update(1)

    # ── 결과 저장 ──────────────────────────────────────────────────
    output = {
        "meta": {
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "provider": provider,
            "model": model,
            "total": len(results),
        },
        "results": results,
    }
    RESULTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    # ── 요약 출력 ──────────────────────────────────────────────────
    success = [r for r in results if r["success"]]
    failed  = [r for r in results if not r["success"]]
    latencies = [r["latency_ms"] for r in success]

    graded = [r for r in results if r.get("correct") is not None and r.get("graded_by")]
    correct = [r for r in graded if r.get("correct")]

    by_cat: dict[str, dict] = {}
    for r in results:
        c = r["category"]
        by_cat.setdefault(c, {"ok": 0, "fail": 0, "graded": 0, "correct": 0})
        by_cat[c]["ok" if r["success"] else "fail"] += 1
        if r.get("correct") is not None and r.get("graded_by"):
            by_cat[c]["graded"] += 1
            if r.get("correct"):
                by_cat[c]["correct"] += 1

    print(f"\n{'='*60}")
    print(f"  결과 요약")
    print(f"{'='*60}")
    print(f"  응답 성공: {len(success)} / 실패: {len(failed)} / 전체: {len(results)}")
    if GRADE and graded:
        print(f"  정답률: {len(correct)}/{len(graded)} ({len(correct)/len(graded)*100:.1f}%)")
    if latencies:
        print(f"  평균 응답 시간: {sum(latencies)//len(latencies)} ms")
        print(f"  최소 / 최대: {min(latencies)} ms / {max(latencies)} ms")
    print(f"\n  카테고리별 결과:")
    for cat, v in sorted(by_cat.items()):
        acc = f" 정답 {v['correct']:3d}/{v['graded']:3d}" if v["graded"] else ""
        bar_s = "█" * v["ok"] + "░" * v["fail"]
        print(f"    {cat:<12} 성공 {v['ok']:3d} / 실패 {v['fail']:3d}{acc}  {bar_s[:24]}")
    print(f"\n  결과 저장: {RESULTS_FILE}")
    print(f"  Phoenix 확인: http://localhost:6006")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
