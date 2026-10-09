# Demo

这只是一个 demo。

事先在手机上登录 Gmail。启动前先手动清空手机后台。

## Android Studio

从 [Android Studio 官网](https://developer.android.com/studio) 下载 Windows 安装包。安装时勾选 Android Studio 和 Android Virtual Device。

SDK 默认在 `C:\Users\<用户名>\AppData\Local\Android\Sdk`。

用 File → Open 打开本仓库根目录，等 Gradle 同步结束，选模拟器或真机，点运行。

模拟器选手机镜像（Pixel，带 Google Play）。真机打开 USB 调试后用数据线连接。

安装后打开 App，点「打开无障碍设置」，启用 Demo。

`MainActivity.kt` 里两行都填电脑的局域网 IP：

```kotlin
private const val EMULATOR_HOST = "your_ip"
private const val REAL_DEVICE_HOST = "your_ip"
```

## 自动脚本

后端在 `backend/gmail_demo.py`。把收件人改成自己的邮箱：

```python
RECIPIENT = "your_email"
```

先启动服务，再在 App 里点「Gmail 固定流程」：

```powershell
pip install -r backend/requirements.txt
python backend/gmail_demo.py
```
