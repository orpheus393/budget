"""pytest fixture: app.py를 import 가능하도록 streamlit/gspread/plotly mock.

app.py는 모듈 레벨에서 streamlit 호출이 많아 그대로 import 시 streamlit
runtime이 필요. 테스트에서는 모든 외부 서비스 호출이 no-op이 되도록 mock.
"""

import os
import sys
from unittest.mock import MagicMock


class _DictMock(dict):
    """dict + attribute access 둘 다 지원 — st.secrets·session_state용."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e


def _install_mocks():
    # streamlit
    st = MagicMock()
    st.secrets = _DictMock({
        "gcp_service_account": _DictMock({
            "type": "service_account", "project_id": "test",
            "private_key_id": "x", "private_key": "x",
            "client_email": "test@test.iam", "client_id": "1",
        }),
        "GOOGLE_SHEET_ID": "test_sheet_id",
    })
    st.session_state = {}
    st.cache_data = MagicMock()
    st.cache_data.return_value = lambda f: f  # @st.cache_data(ttl=...) → identity
    st.columns = lambda *a, **kw: [MagicMock() for _ in range(a[0] if a and isinstance(a[0], int) else len(a[0] if a else [1]))]
    st.tabs = lambda labels: [MagicMock() for _ in labels]
    # st.button / st.form_submit_button은 명시적으로 False 반환 — 모듈 import 시
    # 핸들러 내부가 실행되며 mock 객체가 정규식 등에 흘러가 collection 실패하는 것 방지.
    st.button = lambda *a, **kw: False
    st.form_submit_button = lambda *a, **kw: False
    sys.modules["streamlit"] = st

    # gspread + google.oauth2 — load_data 내부 try/except가 잡아주므로 raise 통과
    sys.modules["gspread"] = MagicMock()
    sys.modules["google"] = MagicMock()
    sys.modules["google.oauth2"] = MagicMock()
    sys.modules["google.oauth2.service_account"] = MagicMock()

    # plotly
    plotly = MagicMock()
    sys.modules["plotly"] = plotly
    sys.modules["plotly.express"] = MagicMock()
    sys.modules["plotly.graph_objects"] = MagicMock()


_install_mocks()

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
