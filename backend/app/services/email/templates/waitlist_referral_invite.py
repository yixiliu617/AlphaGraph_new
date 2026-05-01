def render_waitlist_referral_invite(
    *,
    invitee_name: str | None,
    inviter_name: str,
    inviter_message: str | None,
    signin_url: str,
) -> dict:
    name = invitee_name or "there"
    msg_block = ""
    if inviter_message:
        msg_block = f"""
  <blockquote style="border-left:3px solid #e5e7eb;margin:12px 0;padding:6px 16px;color:#475569;font-style:italic">
    {inviter_message}
  </blockquote>"""
    subject = f"{inviter_name} invited you to AlphaGraph"
    html = f"""<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">{inviter_name} invited you, {name}.</h2>
  <p>You can skip the waitlist — sign in directly with the email this invitation was sent to.</p>
{msg_block}
  <p style="margin:24px 0">
    <a href="{signin_url}" style="background:#5b6cff;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block">Sign in to AlphaGraph</a>
  </p>
  <p style="color:#64748b;font-size:13px">AlphaGraph is the AI-bottleneck research platform for buyside analysts. Trustworthy fundamentals + multilingual transcripts + zero-hallucination chat.</p>
</body></html>"""
    return {"subject": subject, "html": html}
