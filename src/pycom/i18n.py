"""Runtime internationalization for PyCom.

The user-facing strings of the source are written in Simplified Chinese and are
used verbatim when the UI language is ``zh``.  For ``en`` every UI string is
looked up in an English catalog (Chinese is the *key*); anything without a
translation falls back to the Chinese source text.

Language resolution order:
  1. the persisted ``config.language`` value when the user picked one;
  2. otherwise the OS language is detected (:func:`detect_system_language`);
  3. if detection fails or the OS language is not Chinese, English is used.

The module keeps a process-wide *current* language; screens read it when they
build their text, so screens created after a language switch already use the
new language (see ``PyComApp.set_language``).
"""

from __future__ import annotations

import contextlib
import locale
import os

LANG_ZH = "zh"
LANG_EN = "en"
LANGUAGES: tuple[tuple[str, str], ...] = ((LANG_ZH, "中文"), (LANG_EN, "English"))
SUPPORTED = {LANG_ZH, LANG_EN}

# Current language used by tr(); starts as Chinese so behaviour matches the
# pre-i18n app until set_language()/PyComApp resolution runs.
_current: str = LANG_ZH

# zh -> en catalog.  Chinese source strings act as keys, so untranslated
# strings simply fall through to the (Chinese) source text.
_EN: dict[str, str] = {
    # --- theme ---
    "\u4e3b\u9898": "Theme",
    "\u81ea\u52a8": "Auto",
    "\u6d45\u8272": "Light",
    "\u6df1\u8272": "Dark",
    "\u7ec8\u7aef": "Terminal",
    "\u5916\u89c2": "Appearance",
    "\u6587\u4ef6\u4f20\u8f93": "File transfer",
    # --- CLI / argparse ---
    "--bare 直通模式不能与 -s/-f/-e/--hex 等交互启动选项同时使用": (
        "--bare cannot be combined with interactive startup options such as -s/-f/-e/--hex"
    ),
    "--bare 直通模式必须通过 -p/--port 指定串口": "--bare pass-through mode requires -p/--port",
    "-e/--exit-idle 必须为正数": "-e/--exit-idle must be positive",
    "-s/--send、-f/--script 需要先通过 -p/--port 指定端口": (
        "-s/--send and -f/--script require -p/--port first"
    ),
    "显示本帮助并退出": "Show this help and exit",
    "显示版本号并退出": "Show version and exit",
    "连接参数": "Connection options",
    "串口，如 COM3 或 /dev/ttyUSB0": "Serial port, e.g. COM3 or /dev/ttyUSB0",
    "波特率": "Baud rate",
    "数据位": "Data bits",
    "校验位": "Parity bit",
    "停止位": "Stop bits",
    "流控：none / rtscts / xonxoff": "Flow control: none / rtscts / xonxoff",
    "启动动作": "Startup actions",
    "连接后发送字符串命令（支持转义）": "Send this string after connecting (escapes supported)",
    "连接后逐行发送脚本文件（# 开头为注释行）": (
        "Send a script file line by line after connecting (# starts a comment)"
    ),
    "空闲自动退出：连续 SECS 秒未收到任何字节": (
        "Auto-exit after SECS seconds without receiving any byte"
    ),
    "启动即开启 16 进制接收/发送": "Start in HEX receive/send mode",
    "直通模式（--bare，无界面）": "Pass-through mode (--bare, no UI)",
    "隐藏全部界面：把 stdin 接到串口（发送），串口 RX 原样打到 stdout。需通过 -p/--port 指定串口，适合把终端交给 AI agent 等外部进程驱动": (
        "Hide the whole UI: connect stdin to the port (send) and stream port RX to stdout. "
        "Requires -p/--port; ideal for driving the terminal from an external process such as an AI agent"
    ),
    "PyCom: 终端窗口太小（{cols} 列 × {rows} 行），界面无法正常使用。\n请将窗口放大到至少 {mincols} 列 × {minrows} 行后重新运行。\n": (
        "PyCom: terminal too small ({cols} x {rows}) - the UI cannot be used.\n"
        "Please enlarge the window to at least {mincols} x {minrows} and re-run.\n"
    ),
    "minicom 风格的跨平台串口终端：VT/ANSI 渲染、YMODEM/ZMODEM 收发、16 进制接收/发送。\n不带参数启动即进入交互界面（Ctrl+A 打开功能菜单）。": (
        "A minicom-style cross-platform serial terminal: VT/ANSI rendering, YMODEM/ZMODEM transfer, "
        "HEX receive/send.\n"
        "Run without arguments to open the interactive UI (Ctrl+A opens the menu)."
    ),
    '示例：\n  pycom -p COM3 -b 115200\n  pycom -p COM3 -s "AT\\r"\n  pycom -p COM3 -f boot.txt -e 5\n  pycom -p COM3 --hex\n  pycom --bare -p COM3 -b 115200   # 无界面纯直通：stdin→串口，串口→stdout\n\n-s/-f 内容支持 \\n \\r \\t \\xHH 等转义；-e 支持小数秒。--bare 隐藏全部界面，仅供外部进程（如 AI agent）通过标准输入输出驱动。': (
        "Examples:\n"
        "  pycom -p COM3 -b 115200\n"
        '  pycom -p COM3 -s "AT\\r"\n'
        "  pycom -p COM3 -f boot.txt -e 5\n"
        "  pycom -p COM3 --hex\n"
        "  pycom --bare -p COM3 -b 115200   # headless pass-through: stdin->port, port RX->stdout\n"
        "\n"
        "-s/-f accept escapes like \\n \\r \\t \\xHH; -e supports fractional seconds. "
        "--bare hides the whole UI and is driven over stdin/stdout by an external process (e.g. an AI agent)."
    ),
    "bare 直通已连接 {short}（stdin→串口，串口 RX→stdout）": (
        "bare pass-through connected {short} (stdin->port, port RX->stdout)"
    ),
    # --- app / status ---
    "串口终端 - YMODEM": "Serial Terminal - YMODEM",
    "菜单": "Menu",
    "发送": "Send",
    "已连接": "Connected",
    "未连接": "Not connected",
    "已连接 {name}": "Connected to {name}",
    "虚拟回环": "Loopback",
    "回环": "Loopback",
    "回显": "Echo",
    "回绕": "Wrap",
    "捕获": "Capture",
    "HEX：底部输入，点发送": "HEX: type below, click Send",
    "前缀模式: {items} / Esc 取消": "Prefix: {items} / Esc cancel",
    "按 Ctrl+A Z 打开功能菜单": "Press Ctrl+A Z to open the menu",
    "未连接端口：请按 Ctrl+A P 连接后再试": "Not connected: press Ctrl+A P to connect first",
    "连接失败: {err}": "Connection failed: {err}",
    "串口错误: {err}": "Serial error: {err}",
    "启动发送失败: {err}": "Startup send failed: {err}",
    "请先完成/取消进行中的文件传输": "Finish or cancel the running file transfer first",
    "请先在 16 进制输入框输入字节": "Enter bytes in the HEX input box first",
    "已复制 {n} 字符": "Copied {n} characters",
    "没有选中的文本": "No text selected",
    "剪贴板为空": "Clipboard is empty",
    "HEX 模式已开启：在底部输入框输入，点“发送”": "HEX mode enabled: type below and click Send",
    "HEX 模式已关闭": "HEX mode disabled",
    "无法创建捕获文件: {err}": "Cannot create capture file: {err}",
    "开始捕获到 {path}": "Capturing to {path}",
    "退出": "Quit",
    "确定要退出 PyCom 吗？": "Quit PyCom?",
    "是": "Yes",
    "否": "No",
    "未连接串口": "No serial port connected",
    "已有文件传输正在进行": "A file transfer is already in progress",
    "文件不存在: {path}": "File not found: {path}",
    "无法写入 {path}: {err}": "Cannot write {path}: {err}",
    "未选择端口": "No port selected",
    # --- options ---
    "设置": "Options",
    "本地回显": "Local echo",
    "自动回绕": "Auto wrap",
    "接收 LF -> CR+LF": "RX LF -> CR+LF",
    "接收 CR -> CR+LF": "RX CR -> CR+LF",
    "捕获时加时间戳": "Timestamp in capture",
    "发送方向键/功能键 VT 序列": "Send arrow/function keys as VT sequences",
    "16 进制接收/发送（HEX）": "HEX receive/send",
    "回车发送": "Enter sends",
    "退格发送": "Backspace sends",
    "解码字符集": "Decode charset",
    "传输超时(s)": "Transfer timeout (s)",
    "重试次数": "Retries",
    "数据块": "Block size",
    "CR (回车)": "CR (Enter)",
    "LF (换行)": "LF (Newline)",
    "不发送": "Don't send",
    "保存": "Save",
    "取消": "Cancel",
    # --- connection ---
    "串口连接参数": "Serial connection",
    "高级参数": "Advanced",
    "端口": "Port",
    "描述": "Description",
    "校验": "Parity",
    "流控": "Flow control",
    "检测串口…": "Detecting ports…",
    "检测到的串口": "Detected ports",
    "虚拟回环（调试 - 纯回显）": "Loopback (debug - echo)",
    "参数格式错误": "Invalid parameter format",
    "请先在列表中选择端口": "Select a port first",
    "刷新": "Refresh",
    "连接": "Connect",
    "返回": "Back",
    # --- transfer screens ---
    "发送文件": "Send file",
    "接收文件": "Receive file",
    "选择发送协议": "Choose send protocol",
    "选择接收协议": "Choose receive protocol",
    "文件": "File",
    "保存目录": "Save to directory",
    "文件名": "File name",
    "要发送的文件路径": "Path of the file to send",
    "目录": "Directory",
    "留空 = 使用设备发送的文件名": "Leave empty = use the file name sent by the device",
    "浏览…": "Browse…",
    "开始发送": "Start send",
    "开始接收": "Start receive",
    "取消传输": "Cancel transfer",
    "关闭": "Close",
    "就绪。": "Ready.",
    "正在取消…": "Cancelling…",
    "完成": "Done",
    "失败/中止": "Failed/aborted",
    "目录不存在: {dir}": "Directory not found: {dir}",
    "发送 {file} — 等待设备进入接收状态 (先在对端启动接收)…": (
        "Sending {file} - waiting for the device to enter receive mode (start receiving on the peer first)…"
    ),
    "等待设备发送 (请先在对端启动发送)…": (
        "Waiting for the device to send (start sending on the peer first)…"
    ),
    # --- file picker ---
    "选择文件 / 目录": "Select file / directory",
    "上一级": "Parent",
    "选择当前目录": "Select folder",
    # --- menus / language / about ---
    "PyCom - Ctrl+A 功能菜单": "PyCom - Ctrl+A menu",
    "主菜单": "Menu",
    "串口参数": "Serial port",
    "数据传输": "Data transfer",
    "清屏": "Clear screen",
    "16进制 开/关": "HEX on/off",
    "会话捕获 开/关": "Capture on/off",
    "选项": "Options",
    "发送文件(ZMODEM)": "Send file (ZMODEM)",
    "发送文件 - YMODEM": "Send file - YMODEM",
    "接收文件 - YMODEM": "Receive file - YMODEM",
    "发送文件 - ZMODEM": "Send file - ZMODEM",
    "接收文件 - ZMODEM": "Receive file - ZMODEM",
    "捕获开/关": "Capture on/off",
    "语言": "Language",
    "关于": "About",
    "方向键选择；Enter 或功能字母执行；Esc 关闭": (
        "Arrow keys to select; Enter or the key letter to run; Esc to close"
    ),
    "方向键选择；Enter 或功能字母执行；Esc 返回": (
        "Arrow keys to select; Enter or the key letter to run; Esc to go back"
    ),
    "作者: {author}": "Author: {author}",
    "项目主页: {url}": "Project: {url}",
    "开源协议: MIT": "License: MIT",
    "跨平台串口终端，支持 YMODEM / ZMODEM 文件传输、16 进制模式、会话捕获与 VT/ANSI 渲染。": (
        "A cross-platform serial terminal with YMODEM/ZMODEM file transfer, HEX mode, "
        "session capture and VT/ANSI rendering."
    ),
    # --- transfer engines ---
    "准备发送…": "Preparing to send…",
    "发送文件头…": "Sending file header…",
    "传输中…": "Transferring…",
    "结束中…": "Finishing…",
    "开始接收…": "Receiving…",
    "接收中…": "Receiving…",
    "对方中止": "Peer aborted",
    "无响应（对方未进入接收状态）": "No response (the peer did not enter receive mode)",
    "文件头发送失败": "Failed to send file header",
    "用户取消": "User cancelled",
    "EOT 确认失败": "EOT not acknowledged",
    "数据块重传超限（可尝试 128 字节块）": "Data block retransmit limit reached (try 128-byte blocks)",
    "超时": "Timeout",
    "文件头错误": "Bad file header",
    "收到空文件头": "Received an empty file header",
    "拒绝接收 {name}": "Refused to receive {name}",
    "接收失败": "Receive failed",
    "已发送 {n} 字节": "Sent {n} bytes",
    "已接收 {name} ({n} 字节)": "Received {name} ({n} bytes)",
    "对端未确认数据块": "Peer did not ACK data block",
    "对端未确认文件信息": "Peer did not ACK file info",
    "未收到发送方初始化请求": "No init request from the sender",
    "未收到数据起始包": "No data start packet",
    "未收到文件信息": "No file info received",
    "未收到文件名": "No file name received",
    # --- keys ---
    "无效的十六进制片段: {token}": "Invalid hex segment: {token}",
}


def set_language(code: str | None) -> str:
    """Set the current UI language.  Returns the effective code."""
    global _current
    _current = code if code in SUPPORTED else LANG_ZH
    return _current


def get_language() -> str:
    """Return the current UI language code (``"zh"`` or ``"en"``)."""
    return _current


def is_english() -> bool:
    return _current == LANG_EN


def _is_chinese_langcode(code: str) -> bool:
    """True when a language code/tag denotes any Chinese variant."""
    c = code.lower().replace("_", "-").split(".")[0].replace("_", "-")
    return c.startswith("zh")


def detect_system_language() -> str:
    """Detect the operating-system UI language.

    Returns ``"zh"`` when a Chinese locale/UI is found; anything else
    (non-Chinese locale, detection error, no information) falls back to
    ``"en"`` — an English-mode startup is always the safe default.
    """
    # 1) environment locale variables (POSIX, and Windows under some shells)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = os.environ.get(var, "")
        if raw and not raw.strip().upper().startswith("C"):
            with contextlib.suppress(Exception):
                if _is_chinese_langcode(raw):
                    return LANG_ZH
    # 2) Windows UI language (via GetUserDefaultUILanguage LANGID)
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            # primary language id == 0x04 means Chinese
            if (langid & 0x3FF) == 0x04:
                return LANG_ZH
    # 3) Python locale defaults (may return (None, None) on failure)
    with contextlib.suppress(Exception):
        loc = locale.getdefaultlocale()
        if loc and loc[0] and _is_chinese_langcode(loc[0]):
            return LANG_ZH
    return LANG_EN


def tr(text: str, **params: object) -> str:
    """Translate ``text`` into the current UI language.

    When ``params`` are given the translated (or source) text is used as a
    ``str.format`` template with those keyword arguments.
    """
    out = _EN.get(text, text) if _current == LANG_EN else text
    if params:
        with contextlib.suppress(KeyError, IndexError, ValueError):
            out = out.format(**params)
    return out
