"""AI은행원 대화는 주인(아이디)만 보고·지울 수 있다.

대화에는 이체 받는 사람·금액·잔액이 남는다. 예전에는 대화 파일에 주인이 없어서 로그인한
누구에게나 남의 대화가 목록·검색에 떴다.
"""
import pytest

import storage


@pytest.fixture(autouse=True)
def tmp_conv_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CONV_DIR", tmp_path)


def make(owner, text, guest=False):
    conv = storage.new_conversation()
    conv["owner"] = owner
    conv["guest"] = guest
    conv["messages"] = [{"role": "user", "content": text}]
    storage.save_conversation(conv)
    return conv


def test_목록은_내_대화만():
    mine = make("demo", "이영희한테 5만원 보내줘")
    make("admin", "김철수한테 3만원 보내줘")
    assert [c["id"] for c in storage.list_conversations("demo")] == [mine["id"]]


def test_검색도_내_대화_안에서만():
    make("admin", "김철수한테 3만원 보내줘")
    assert storage.list_conversations("demo", query="김철수") == []


def test_주인_없는_대화와_비로그인은_목록이_비어있다():
    make(None, "예전 대화")
    assert storage.list_conversations("demo") == []
    assert storage.list_conversations(None) == []


def test_남의_대화는_지울_수_없다():
    other = make("admin", "김철수한테 3만원 보내줘")
    storage.delete_conversation(other["id"], "demo")
    assert storage.load_conversation(other["id"]) is not None
    storage.delete_conversation(other["id"], "admin")
    assert storage.load_conversation(other["id"]) is None


def test_대화_id_형식이_아니면_열지_않는다():
    assert storage.load_conversation("../config") is None
    assert storage.load_conversation("") is None
