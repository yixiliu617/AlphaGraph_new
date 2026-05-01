def render_waitlist_approved(*, full_name: str | None, signin_url: str) -> dict:
    name = full_name or "there"
    subject = "You’re approved for AlphaGraph"
    html = f"""<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">Welcome, {name}.</h2>
  <p>You’re approved. Click below to sign in with the Google or Microsoft account you used to apply.</p>
  <p style="margin:24px 0">
    <a href="{signin_url}" style="background:#5b6cff;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block">Sign in to AlphaGraph</a>
  </p>
  <p style="color:#64748b;font-size:13px">First sign-in takes you through a quick 6-step setup so we can tailor your dashboard. About 60 seconds.</p>
  <p style="color:#94a3b8;font-size:12px;margin-top:24px">If the button doesn’t work, copy this link: <code>{signin_url}</code></p>
</body></html>"""
    return {"subject": subject, "html": html}
