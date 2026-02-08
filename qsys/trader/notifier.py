
import requests
import json
import logging
from typing import Dict, Any

log = logging.getLogger(__name__)

class Notifier:
    """
    Notifier service for sending alerts and reports via Enterprise WeChat Webhook.
    """
    
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url
    
    def send_text(self, content: str, mentioned_list: list = None):
        """
        Send plain text message.
        """
        if not self.webhook_url:
            log.warning("No Webhook URL configured. Notification skipped.")
            return

        data = {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_list": mentioned_list or []
            }
        }
        self._post(data)

    def send_markdown(self, content: str):
        """
        Send Markdown message.
        """
        if not self.webhook_url:
            log.warning("No Webhook URL configured. Notification skipped.")
            return

        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        self._post(data)

    def _post(self, data: Dict[str, Any]):
        try:
            headers = {'Content-Type': 'application/json'}
            resp = requests.post(self.webhook_url, headers=headers, data=json.dumps(data), timeout=5)
            resp.raise_for_status()
            log.info("Notification sent successfully.")
        except Exception as e:
            log.error(f"Failed to send notification: {e}")

    def send_daily_report(self, date: str, assets: float, trade_count: int, pnl_pct: float):
        """
        Send a standardized daily report.
        """
        color = "green" if pnl_pct >= 0 else "red"
        msg = f"""
### 📊 SysQ Daily Report ({date})
> **Assets**: {assets:,.2f}
> **Trades**: {trade_count}
> **PnL**: <font color="{color}">{pnl_pct:.2%}</font>
        """
        self.send_markdown(msg.strip())

    def send_plan(self, date: str, plan_df):
        """
        Send trading plan.
        """
        if plan_df.empty:
            self.send_text(f"📅 Plan for {date}: No actions.")
            return

        # Format DataFrame to Markdown
        md_table = plan_df.to_markdown(index=False)
        msg = f"""
### 🚀 Trading Plan for {date}
{md_table}
        """
        self.send_markdown(msg.strip())
