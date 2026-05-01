def render_waitlist_received(*, full_name: str | None) -> dict:
    name = full_name or "there"
    subject = "AlphaGraph — application received"
    html = f"""<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">Thanks, {name}.</h2>
  <p>We’ve received your AlphaGraph access request. Our team reviews each application personally — usually within 1 business day.</p>
  <p>You’ll get a follow-up email once you’re approved. In the meantime, follow <a href="https://alphagraph.com">@alphagraph_ai</a> for product updates.</p>
  <p style="color:#64748b;font-size:13px;margin-top:24px">— AlphaGraph team</p>
</body></html>"""
    return {"subject": subject, "html": html}
