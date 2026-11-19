# Screen Agent

手机连上后端后，后端可以主动要一张当前截图，也可以下发一条已定义的操作指令。手机按指令类型调用对应的无障碍接口。

```powershell
conda activate soft
python -m pip install -r backend/requirements.txt
python backend/main.py
```

Android Studio 打开本目录并安装到手机。开启 **Screen Agent** 无障碍服务，在 App 里连接后端。USB 调试时先执行 `adb reverse tcp:8000 tcp:8000`，服务器填 `http://127.0.0.1:8000`。

要截图时请求 `POST /capture`。要执行操作时把 `backend/contract.py` 里的一种指令发给 `POST /execute`。
