"""发送签到完成提醒。"""

import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.errors import HeaderParseError
from email.utils import formatdate, make_msgid


RESULT_LABELS = {"signed": "签到成功", "already_signed": "今日已签到", "failed": "签到失败"}


def mailbox(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("请填写有效的邮箱地址。")
    value = value.strip()
    if not 0 < len(value) <= 254 or not value.isascii() or any(ord(char) < 32 for char in value):
        raise ValueError("请填写有效的邮箱地址。")
    try:
        address = Address(addr_spec=value)
    except (ValueError, HeaderParseError):
        raise ValueError("请填写有效的邮箱地址。") from None
    if not address.username or not address.domain or address.addr_spec != value:
        raise ValueError("请填写有效的邮箱地址。")
    return value


def send_reminder(config, password: str, recipient: str, name: str, source: str, state: str, finished_at: str) -> None:
    result = RESULT_LABELS[state]
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = f"PracticeTool · {result}"
    message["Date"] = formatdate()
    message["Message-ID"] = make_msgid(domain=config["sender"].rsplit("@", 1)[1])
    message.set_content(
        f"账号：{name}\n操作：{source}\n时间：{finished_at[:19].replace('T', ' ')}（北京时间）\n"
        f"结果：{result}\n\n打开 PracticeTool 调度中心查看详情。"
    )

    context = ssl.create_default_context()
    host, port = config["host"], config["port"]
    if config["security"] == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=8, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=8)
    try:
        if config["security"] == "starttls":
            client.starttls(context=context)
        client.login(config["sender"], password)
        client.send_message(message)
    finally:
        client.close()
