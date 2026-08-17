"""指令通道可靠性測試（離線）。

驗證五件事：
  ① job 執行到一半掛掉時，指令不會消失（位移要跑完才推進）
  ② 每次都失敗的指令不會無限重試
  ③ 指令等待時間會回報給你
  ④ 帳號 JSON 壞掉時，指令通道仍然活著（否則你連問都問不到哪裡壞了）
  ⑤ 帳號清空時同上

用法：
    python tools/selftest_reliability.py
"""

import json, os, sys, tempfile, contextlib, io, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import selftest as st

UPDATES={"list":[]}
def do_GET(self):
    if "/getUpdates" in self.path: return self._send(200,{"ok":True,"result":UPDATES["list"]})
    return st.Handler._graph(self,"GET")
st.Handler.do_GET = do_GET
server, base = st.start_server()

from e5keeper import auth, graph, history, notify, telegram_poll
from e5keeper.config import load_settings
auth.LOGIN_HOST=base; graph.GRAPH_V1=base+"/v1.0"; graph.GRAPH_BETA=base+"/beta"; notify.API_ROOT=base
tmp=Path(tempfile.mkdtemp())
history.HISTORY_DIR=tmp/"h"; history.STATUS_FILE=tmp/"S.md"
telegram_poll.STATE_DIR=tmp/"st"; telegram_poll.OFFSET_FILE=tmp/"st/off.json"
COMMITS=[]
history.commit_and_push=lambda m,p=None,**k:(COMMITS.append(m),True)[1]

os.environ["E5_ACCOUNTS"]=json.dumps([{"alias":"A","email":"a@b.com","mode":"delegated",
  "tenant":"common","client_id":"c","refresh_token":"R"*60}])
os.environ.update({"TELEGRAM_BOT_TOKEN":"1:F","TELEGRAM_CHAT_ID":"99"})
os.environ.pop("GITHUB_ACTIONS",None)

bad=0
def case(n,ok,d=""):
    global bad
    print(f"  {'✅' if ok else '❌'} {n}"+(f"　→ {d}" if not ok and d else "")); bad+=0 if ok else 1
def msg(uid,text,ago=0):
    return {"update_id":uid,"message":{"chat":{"id":99},"from":{"id":99,"username":"me"},
            "text":text,"date":int(time.time())-ago}}
def state(): return json.loads(telegram_poll.OFFSET_FILE.read_text())

print("① job 在執行指令途中掛掉 → 指令不能消失")
UPDATES["list"]=[msg(500,"/ping")]
s=load_settings()
orig=telegram_poll._dispatch
def boom(*a,**k): raise SystemExit("模擬 job 被逾時砍掉")
telegram_poll._dispatch=boom
try:
    with contextlib.redirect_stdout(io.StringIO()): telegram_poll.poll(s)
except SystemExit: pass
telegram_poll._dispatch=orig
case("位移沒有被推進（指令保住了）", state()["offset"]==0, str(state()))
case("嘗試次數有被記錄", state()["attempts"].get("500")==1, str(state()))
with contextlib.redirect_stdout(io.StringIO()): telegram_poll.poll(s)
case("下一輪重跑並成功", any("pong" in m for m in st.SENT))
case("成功後位移才推進", state()["offset"]==500, str(state()))
case("成功後嘗試紀錄被清掉", state()["attempts"]=={}, str(state()))

print("\n② 毒藥指令不能無限重試")
telegram_poll.OFFSET_FILE.write_text(json.dumps({"offset":0,"attempts":{"600":3}}))
UPDATES["list"]=[msg(600,"/ping")]; st.SENT.clear()
with contextlib.redirect_stdout(io.StringIO()): telegram_poll.poll(s)
case("超過上限的指令被跳過並告知", any("連續失敗" in m for m in st.SENT), str(st.SENT)[:120])

print("\n③ 等待時間會回報")
telegram_poll.OFFSET_FILE.write_text(json.dumps({"offset":0,"attempts":{}}))
UPDATES["list"]=[msg(700,"/run",ago=930)]; st.SENT.clear()
s.raw["run"]["api_delay_seconds"]=[0,0]; s.raw["run"]["account_delay_seconds"]=[0,0]
with contextlib.redirect_stdout(io.StringIO()): telegram_poll.poll(s)
case("告知等了 15 分鐘", any("等了 15 分" in m for m in st.SENT), str([m[:80] for m in st.SENT]))

print("\n④ 帳號 JSON 壞掉時，指令通道仍要活著")
from e5keeper.main import cmd_poll
os.environ["E5_ACCOUNTS"]="{壞掉的 json"
telegram_poll.OFFSET_FILE.write_text(json.dumps({"offset":0,"attempts":{}}))
UPDATES["list"]=[msg(800,"/help")]; st.SENT.clear()
class A: pass
try:
    with contextlib.redirect_stdout(io.StringIO()): rc=cmd_poll(A())
    case("poll 沒有崩潰", rc==0, f"rc={rc}")
except Exception as ex:
    case("poll 沒有崩潰", False, f"{ex.__class__.__name__}: {ex}")
case("有警告帳號設定壞了", any("E5_ACCOUNTS 設定有問題" in m for m in st.SENT))
case("/help 仍然回得出來", any("指令說明" in m for m in st.SENT), str([m[:40] for m in st.SENT]))

print("\n⑤ 帳號為空時，指令通道仍要活著")
os.environ["E5_ACCOUNTS"]="[]"
telegram_poll.OFFSET_FILE.write_text(json.dumps({"offset":0,"attempts":{}}))
UPDATES["list"]=[msg(900,"/list")]; st.SENT.clear()
with contextlib.redirect_stdout(io.StringIO()): rc=cmd_poll(A())
case("/list 回報目前沒有帳號", any("沒有任何帳號" in m for m in st.SENT), str([m[:40] for m in st.SENT]))

print("\n"+("✅ 可靠性強化測試全數通過" if not bad else f"❌ 有 {bad} 項沒通過"))
server.shutdown(); sys.exit(1 if bad else 0)
