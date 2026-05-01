def render_admin_new_waitlist_signup(
    *,
    applicant_email: str,
    applicant_name: str | None,
    role: str | None,
    firm: str | None,
) -> dict:
    name = applicant_name or "(no name given)"
    role_str = role or "(no role)"
    firm_str = firm or "(no firm)"
    subject = f"[AlphaGraph waitlist] {applicant_email}"
    html = f"""<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h3 style="margin:0 0 8px 0">New waitlist application</h3>
  <table style="font-size:13px;border-collapse:collapse">
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Name</td><td>{name}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Email</td><td><code>{applicant_email}</code></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Role</td><td>{role_str}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Firm</td><td>{firm_str}</td></tr>
  </table>
  <p style="margin-top:18px"><a href="https://alphagraph.com/admin/waitlist">Review queue →</a></p>
</body></html>"""
    return {"subject": subject, "html": html}
