# Demo

这只是一个 demo。

事先在手机上登录 Gmail。启动前先手动清空手机后台。

## 安装

从 [Android Studio 官网](https://developer.android.com/studio) 下载 Windows 安装包。安装时勾选 Android Studio 和 Android Virtual Device。

SDK 默认在 `C:\Users\<用户名>\AppData\Local\Android\Sdk`。

用 File → Open 打开本仓库根目录，等 Gradle 同步结束，选模拟器或真机，点运行。

模拟器选手机镜像（Pixel，带 Google Play）。真机打开 USB 调试后用数据线连接。

安装后打开 App，点「打开无障碍设置」，启用 Demo。

## 改成自己的

`MainActivity.kt` 里两行都填电脑当前的局域网 IP，然后重新安装 App：

```kotlin
private const val EMULATOR_HOST = "your_ip"
private const val REAL_DEVICE_HOST = "your_ip"
```

App 里的提示词改成自己的邮箱和内容，例如：

```text
使用gmail发送邮件，发送给your_email@example.com，内容是demo
```

DeepSeek 的密钥不要写进代码。在启动后端的终端里设置：

```powershell
$env:DEEPSEEK_API_KEY = "your_api_key"
```

固定流程的收件人在 `backend/gmail_demo.py`：

```python
RECIPIENT = "your_email@example.com"
```

## 启动

```powershell
pip install -r backend/requirements.txt
$env:DEEPSEEK_API_KEY = "your_api_key"
python backend/gmail_demo.py
```

服务起来后，在 App 里点「按提示执行」。模型会按提示词操作手机。

「Gmail 固定流程」不调用模型，按写好的步骤发信。

## 核心逻辑

后端向手机要当前页面，手机返回界面 XML。后端读 XML 里的文字和坐标，决定下一步，再把命令发回手机。手机收到后执行。

命令有 `home`、`tap`、`swipe_down`、`input_text`、回车。

按提示执行时，提示词会一直交给模型。模型根据当前界面决定一条命令，电脑再发给手机执行。
