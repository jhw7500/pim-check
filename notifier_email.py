"""
notifier_email.py — FAIL 시 이메일 알림

smtplib 기반. 외부 의존성 없음.
~/.pim-check.yaml의 email 설정을 사용.
"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText


def send_email(
    smtp_host: str,
    smtp_port: int,
    sender: str,
    password: str,
    recipients: list[str],
    subject: str,
    body: str,
    use_tls: bool = True,
) -> bool:
    """이메일을 발송한다. 성공 시 True."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())
        server.quit()
        return True
    except Exception:
        return False


def send_fail_email(
    email_config: dict,
    results: list,
    case_name: str | None,
    host: str = "",
) -> bool:
    """FAIL 결과를 이메일로 발송한다."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_checks = [r for r in results if not r["passed"] and "known_issue" not in r]

    subject = f"[pim-check FAIL] {case_name or 'healthcheck'} on {host} ({passed}/{total})"
    lines = [subject, ""]
    for r in failed_checks:
        lines.append(f"  FAIL: {r['name']} — {r.get('reason', '')}")
    body = "\n".join(lines)

    return send_email(
        smtp_host=email_config.get("smtp_host", "smtp.gmail.com"),
        smtp_port=email_config.get("smtp_port", 587),
        sender=email_config.get("sender", ""),
        password=email_config.get("password", ""),
        recipients=email_config.get("recipients", []),
        subject=subject,
        body=body,
        use_tls=email_config.get("use_tls", True),
    )
