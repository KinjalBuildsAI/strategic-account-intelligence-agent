import os
import json
import time
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from io import BytesIO

import requests
import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

from replit import db  # Replit Key-Value Store (credits/codes/users)


# =========================
# Constants you can edit
# =========================

APP_NAME_DEFAULT = "Strategic Account Intelligence Agent"
TZ = ZoneInfo("America/New_York")

# Exact starter codes for THIS week (ISO week based).
# These are optional. You can delete them after testing and only use Admin-generated codes.
SEED_CODES = {
  # Recruiter/Hiring Manager
  "REC-2026W02-7H3Q9A": {"tier": "recruiter"},
  "REC-2026W02-N4D2KP": {"tier": "recruiter"},
  "REC-2026W02-V8M1TZ": {"tier": "recruiter"},

  # General
  "GEN-2026W02-2J9L4C": {"tier": "general"},
  "GEN-2026W02-Q6R7W3": {"tier": "general"},
  "GEN-2026W02-X1P8FS": {"tier": "general"},

  # VIP / High-caliber
  "VIP-2026W02-5K2Y9N": {"tier": "vip"},
  "VIP-2026W02-H7T1RD": {"tier": "vip"},
  "VIP-2026W02-M3C6UZ": {"tier": "vip"},
}

DEFAULT_FREE_CREDITS = 2


# =========================
# Secrets (must exist in Replit Secrets)
# =========================

def must_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        st.error(f"Missing required Secret: {key}. Add it in Replit → Tools → Secrets.")
        st.stop()
    return val


def optional_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# =========================
# Time helpers
# =========================

def now_et() -> datetime:
    return datetime.now(TZ)


def iso_week_id(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}W{w:02d}"


def week_expiry_sunday_235959(dt: datetime) -> datetime:
    # ISO: Mon=1 ... Sun=7
    days_to_sun = 7 - dt.isoweekday()
    sunday = (dt + timedelta(days=days_to_sun)).replace(hour=23, minute=59, second=59, microsecond=0)
    return sunday


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# =========================
# Replit DB key helpers
# =========================

def k_user(email: str) -> str:
    return f"user:{email.strip().lower()}"


def k_code(code: str) -> str:
    return f"code:{code.strip()}"


def k_history(email: str) -> str:
    return f"history:{email.strip().lower()}"


def k_cache(hash_key: str) -> str:
    return f"cache:{hash_key}"


# =========================
# Data models
# =========================

@dataclass
class CodeRecord:
    code: str
    email: str  # bound email (or "" if not bound yet)
    tier: str   # recruiter | general | vip
    week_id: str
    expires_at: str
    created_at: str

@dataclass
class UserRecord:
    email: str
    credits: int
    tier: str
    created_at: str
    last_login: str


# =========================
# DB operations
# =========================

def db_get(key: str, default=None):
    return db[key] if key in db else default


def db_set(key: str, value):
    db[key] = value


def ensure_user(email: str, tier: str = "general") -> UserRecord:
    key = k_user(email)
    now = to_iso(now_et())
    if key not in db:
        rec = {
            "email": email.lower(),
            "credits": DEFAULT_FREE_CREDITS,
            "tier": tier,
            "created_at": now,
            "last_login": now,
        }
        db_set(key, rec)
    else:
        rec = db_get(key)
        rec["last_login"] = now
        if tier and not rec.get("tier"):
            rec["tier"] = tier
        db_set(key, rec)

    rec = db_get(key)
    return UserRecord(**rec)


def update_user_credits(email: str, new_credits: int):
    key = k_user(email)
    rec = db_get(key)
    if not rec:
        rec = {"email": email.lower(), "credits": new_credits, "tier": "general",
               "created_at": to_iso(now_et()), "last_login": to_iso(now_et())}
    rec["credits"] = int(new_credits)
    db_set(key, rec)


def decrement_credit(email: str) -> int:
    key = k_user(email)
    rec = db_get(key)
    if not rec:
        rec = {"email": email.lower(), "credits": DEFAULT_FREE_CREDITS, "tier": "general",
               "created_at": to_iso(now_et()), "last_login": to_iso(now_et())}
    rec["credits"] = max(0, int(rec.get("credits", 0)) - 1)
    db_set(key, rec)
    return rec["credits"]


def save_history(email: str, payload: dict):
    key = k_history(email)
    hist = db_get(key, [])
    hist.insert(0, payload)
    hist = hist[:25]  # keep last 25
    db_set(key, hist)


def get_history(email: str):
    return db_get(k_history(email), [])


# =========================
# Code issuance + validation
# =========================

def create_code_for_email(email: str, tier: str) -> CodeRecord:
    dt = now_et()
    week_id = iso_week_id(dt)
    expires = week_expiry_sunday_235959(dt)
    token = secrets.token_hex(3).upper()  # 6 hex chars
    prefix = "GEN"
    if tier == "recruiter":
        prefix = "REC"
    elif tier == "vip":
        prefix = "VIP"
    code = f"{prefix}-{week_id}-{token}"

    rec = {
        "code": code,
        "email": email.lower().strip(),
        "tier": tier,
        "week_id": week_id,
        "expires_at": to_iso(expires),
        "created_at": to_iso(dt),
    }
    db_set(k_code(code), rec)
    return CodeRecord(**rec)


def seed_demo_codes_if_missing():
    # Seeds the exact codes in SEED_CODES, unbound (email="") for easy testing.
    dt = now_et()
    week_id = iso_week_id(dt)
    expires = week_expiry_sunday_235959(dt)

    created = 0
    for code, meta in SEED_CODES.items():
        key = k_code(code)
        if key in db:
            continue
        rec = {
            "code": code,
            "email": "",  # unbound until you assign it
            "tier": meta["tier"],
            "week_id": week_id,
            "expires_at": to_iso(expires),
            "created_at": to_iso(dt),
        }
        db_set(key, rec)
        created += 1
    return created, week_id, to_iso(expires)


def bind_unbound_code_to_email(code: str, email: str):
    key = k_code(code)
    rec = db_get(key)
    if not rec:
        return False, "Code not found."
    if rec.get("email"):
        return False, "Code already bound to an email."
    rec["email"] = email.lower().strip()
    db_set(key, rec)
    return True, "Bound successfully."


def validate_login_code(email: str, code: str):
    email = email.lower().strip()
    code = code.strip()

    rec = db_get(k_code(code))
    if not rec:
        return False, "Invalid code."

    # Weekly expiry check
    dt = now_et()
    current_week = iso_week_id(dt)
    if rec.get("week_id") != current_week:
        return False, "Code expired (week changed). Request a new code."

    # Time expiry check (extra safety)
    try:
        if parse_iso(rec.get("expires_at")) < dt:
            return False, "Code expired (time window ended). Request a new code."
    except Exception:
        return False, "Code expiry is malformed. Ask the owner for a new code."

    # Email binding rules
    if rec.get("email"):
        if rec["email"] != email:
            return False, "Code does not match this email. Use the email you requested access with."
    else:
        # First valid use binds the code to that email
        rec["email"] = email
        db_set(k_code(code), rec)

    return True, rec


# =========================
# Make.com webhook posting
# =========================

def post_webhook(url: str, payload: dict):
    try:
        r = requests.post(url, json=payload, timeout=12)
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


# =========================
# Perplexity client
# =========================

def perplexity_brief(company: str, website: str, persona: str, value_prop: str, initiative: str, region: str, competitor: str, model: str):
    api_key = must_env("KinjalsSecretAPIKey")

    endpoint = "https://api.perplexity.ai/chat/completions"

    system = (
        "You are a strategic enterprise sales/account planning assistant. "
        "You must be proof-first: every important claim must be supported by a citation. "
        "Be concise, specific, and avoid generic fluff."
    )

    user = f"""
Create a 1-page, proof-first strategic account intelligence brief.

Inputs:
- Company: {company}
- Website: {website}
- Target persona: {persona}
- My value proposition: {value_prop}
Optional context:
- Initiative: {initiative or "N/A"}
- Region/BU: {region or "N/A"}
- Competitor(s): {competitor or "N/A"}

Requirements:
1) Return STRICT JSON (no markdown, no extra text).
2) Include freshness stamps: "generated_at" (ISO) and "search_recency" (e.g., month).
3) For each module, include:
   - "bullets": 3–7 bullets
   - "confidence": number 0 to 1
   - "evidence": array of objects with {{"url","title","snippet","date"}}.
   Evidence snippets should be short and directly support the bullet.
4) Modules (exact keys):
   - account_summary
   - top_3_priorities
   - strategic_blockers
   - news_signal
   - recommended_messaging
   - discovery_questions
   - risks_objections
   - next_step_email

Notes:
- Prefer public sources: SEC filings, company investor relations, reputable news.
- If information is uncertain, say so and lower confidence.

Return JSON with this shape:
{{
  "generated_at": "...",
  "search_recency": "month",
  "company": "...",
  "persona": "...",
  "modules": {{
    "account_summary": {{"bullets":[], "confidence":0.0, "evidence":[]}},
    ...
  }}
}}
""".strip()

    payload = {
        "model": model,  # sonar is cheapest; sonar-pro is stronger
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
        "search_recency_filter": "month",
        ###"response_format": {"type": "json_object"},
        "web_search_options": {"search_context_size": "low"},
        "return_related_questions": False,
        "return_images": False,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    r = requests.post(endpoint, headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
    	raise ValueError(f"Perplexity error {r.status_code}: {r.text}")
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]

    # content should already be JSON due to response_format, but we still parse to be safe
    brief = json.loads(content)

    # Attach top-level API metadata for debugging / transparency
    brief["_api_meta"] = {
        "model": data.get("model"),
        "created": data.get("created"),
        "usage": data.get("usage", {}),
        "search_results": data.get("search_results", []),
    }
    return brief


# =========================
# PDF generation
# =========================

def brief_to_pdf_bytes(brief: dict) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    x = 50
    y = height - 50

    def draw_line(text, size=11, leading=14):
        nonlocal y
        c.setFont("Helvetica", size)
        for line in wrap_text(text, 95):
            c.drawString(x, y, line)
            y -= leading
        y -= 6

    def heading(text):
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x, y, text)
        y -= 18

    heading("Strategic Account Intelligence Brief")
    draw_line(f"Company: {brief.get('company','')}")
    draw_line(f"Persona: {brief.get('persona','')}")
    draw_line(f"Generated: {brief.get('generated_at','')}")
    draw_line(f"Search recency: {brief.get('search_recency','')}")

    modules = brief.get("modules", {})

    def module_block(title, key):
        nonlocal y
        if y < 160:
            c.showPage()
            y = height - 50
        heading(title)
        m = modules.get(key, {})
        bullets = m.get("bullets", [])
        conf = m.get("confidence", None)
        if conf is not None:
            draw_line(f"Confidence: {conf}")
        for b in bullets[:6]:
            draw_line(f"- {b}", size=11)

        evidence = m.get("evidence", [])[:3]
        if evidence:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x, y, "Top evidence:")
            y -= 14
            c.setFont("Helvetica", 10)
            for ev in evidence:
                draw_line(f"• {ev.get('title','')} ({ev.get('url','')})", size=9, leading=11)

    module_block("Account Summary", "account_summary")
    module_block("Top 3 Priorities", "top_3_priorities")
    module_block("Strategic Blockers", "strategic_blockers")
    module_block("News Signal (Why Now)", "news_signal")
    module_block("Recommended Messaging", "recommended_messaging")
    module_block("Discovery Questions", "discovery_questions")
    module_block("Risks / Objections", "risks_objections")
    module_block("Next-Step Email Draft", "next_step_email")

    c.save()
    return buf.getvalue()


def wrap_text(text: str, max_chars: int):
    words = str(text).split()
    lines = []
    cur = []
    count = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if count + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            count = len(w)
        else:
            cur.append(w)
            count += add
    if cur:
        lines.append(" ".join(cur))
    return lines


# =========================
# UI / App
# =========================

def init_state():
    st.session_state.setdefault("logged_in", False)
    st.session_state.setdefault("email", "")
    st.session_state.setdefault("tier", "general")
    st.session_state.setdefault("is_admin", False)


def logout():
    st.session_state["logged_in"] = False
    st.session_state["email"] = ""
    st.session_state["tier"] = "general"
    st.session_state["is_admin"] = False


def render_request_access(make_signup_url: str):
    st.subheader("Request Access")
    st.write("Fill this out to receive a time-limited code. Codes expire weekly and are tied to your email.")

    with st.form("request_access_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        first = col1.text_input("First name *")
        last = col2.text_input("Last name *")
        email = st.text_input("Email *")
        company = st.text_input("Company *")
        title = st.text_input("Title *")
        linkedin = st.text_input("LinkedIn profile link *")
        reason = st.text_area("Why are you here? (1–2 sentences) *")

        submitted = st.form_submit_button("Submit request")
        if submitted:
            required = [first, last, email, company, title, linkedin, reason]
            if any(not x.strip() for x in required):
                st.error("Please complete all required fields (*)")
                return

            payload = {
                "first_name": first.strip(),
                "last_name": last.strip(),
                "email": email.strip(),
                "company": company.strip(),
                "title": title.strip(),
                "linkedin": linkedin.strip(),
                "reason": reason.strip(),
                "submitted_at": to_iso(now_et()),
            }
            code, body = post_webhook(make_signup_url, payload)
            if code and 200 <= code < 300:
                st.success("Submitted! The owner will email you a code shortly.")
            else:
                st.error(f"Submission failed. (status={code}) {body}")


def render_login(owner_email: str, owner_code: str):
    st.subheader("Log in")
    st.write("Enter your email + access code (the owner emails this to you).")

    with st.form("login_form"):
        email = st.text_input("Email", value=st.session_state.get("email", "")).strip().lower()
        code = st.text_input("Access code", type="password").strip()
        submitted = st.form_submit_button("Log in")

        if not submitted:
            return

        if not email or not code:
            st.error("Please enter both email and access code.")
            return

        # Owner bypass (admin)
        if email == owner_email.lower().strip() and code == owner_code:
            st.session_state["logged_in"] = True
            st.session_state["email"] = email
            st.session_state["tier"] = "owner"
            st.session_state["is_admin"] = True
            ensure_user(email, tier="owner")
            st.success("Logged in as owner/admin.")
            st.rerun()

        ok, rec_or_msg = validate_login_code(email, code)
        if not ok:
            st.error(rec_or_msg)
            return

        code_rec = rec_or_msg
        tier = code_rec.get("tier", "general")
        user = ensure_user(email, tier=tier)

        st.session_state["logged_in"] = True
        st.session_state["email"] = email
        st.session_state["tier"] = user.tier
        st.session_state["is_admin"] = False
        st.success("Logged in.")
        st.rerun()


def render_admin(admin_password: str):
    st.subheader("Admin")

    # simple password gate so only you can use admin tools even if you share owner code accidentally
    with st.expander("Admin unlock"):
        pw = st.text_input("Admin password", type="password")
        if pw != admin_password:
            st.warning("Enter admin password to unlock admin tools.")
            return

    st.write("### 1) Seed this week’s demo codes (optional)")
    if st.button("Seed demo codes now"):
        created, week_id, expires = seed_demo_codes_if_missing()
        st.success(f"Seeded {created} code(s). Week={week_id}, expires={expires}")

    st.write("### 2) Create a NEW code for a specific email")
    with st.form("create_code_form"):
        email = st.text_input("Email to issue code to")
        tier = st.selectbox("Category", ["recruiter", "general", "vip"])
        submit = st.form_submit_button("Create code")
        if submit:
            if not email.strip():
                st.error("Email is required.")
            else:
                rec = create_code_for_email(email.strip(), tier)
                st.success("Code created. Copy/paste this into your email:")
                st.code(rec.code)

    st.write("### 3) Bind an unbound seeded code to an email")
    with st.form("bind_code_form"):
        code = st.text_input("Existing code (from seeded list)")
        email = st.text_input("Bind to email")
        submit = st.form_submit_button("Bind code")
        if submit:
            ok, msg = bind_unbound_code_to_email(code.strip(), email.strip())
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    st.write("### 4) Add credits to a user (manual override)")
    with st.form("add_credits_form"):
        email = st.text_input("User email")
        credits = st.number_input("Set credits to", min_value=0, max_value=999, value=2)
        submit = st.form_submit_button("Update credits")
        if submit:
            update_user_credits(email.strip(), int(credits))
            st.success("Credits updated.")

    st.write("### 5) Quick lookup")
    lookup = st.text_input("Lookup user email")
    if lookup.strip():
        rec = db_get(k_user(lookup.strip().lower()))
        st.json(rec if rec else {"error": "Not found"})


def render_agent(make_contact_url: str):
    email = st.session_state["email"]
    user = ensure_user(email, tier=st.session_state.get("tier", "general"))

    st.subheader("Agent")
    st.write(f"Logged in as: **{email}**")
    st.write(f"Credits remaining: **{user.credits}**")

    if user.credits <= 0:
        st.error("You have 0 credits remaining. This demo disables runs after two free uses.")

        st.write("### Buy More Credits (planned)")
        if st.button("Buy More Credits ($5 for 2 credits)"):
            st.info("Planned feature. Please contact the owner to request more credits.")

        st.write("### Contact owner")
        with st.form("contact_owner_form", clear_on_submit=True):
            from_email = st.text_input("Your email", value=email)
            msg = st.text_area("Message")
            submit = st.form_submit_button("Send")
            if submit:
                payload = {
                    "email": from_email.strip(),
                    "message": msg.strip(),
                    "submitted_at": to_iso(now_et()),
                }
                code, body = post_webhook(make_contact_url, payload)
                if code and 200 <= code < 300:
                    st.success("Sent.")
                else:
                    st.error(f"Failed. (status={code}) {body}")
        return

    st.write("### Inputs")
    with st.form("agent_inputs"):
        company = st.text_input("Company name *", placeholder="BioMarin")
        website = st.text_input("Company website *", placeholder="https://www.biomarin.com")
        persona = st.text_input("Target persona *", placeholder="CIO")
        value_prop = st.text_area("Your value proposition *", placeholder="We provide AI-driven logistics optimization that reduces carbon footprints.")
        st.write("Optional (improves personalization)")
        initiative = st.text_input("Initiative (optional)", placeholder="ERP modernization / cost takeout / supply chain resilience")
        region = st.text_input("Region / BU (optional)", placeholder="US Commercial / EU Ops / Manufacturing")
        competitor = st.text_input("Competitors you’re up against (optional)", placeholder="Vendor A, Vendor B")

        model = st.selectbox("Perplexity model", ["sonar", "sonar"], index=0)
        run = st.form_submit_button("Run Agent (uses 1 credit)")

    if not run:
        return

    required = [company, website, persona, value_prop]
    if any(not x.strip() for x in required):
        st.error("Please complete all required fields (*)")
        return

    # Cache key (to reduce Perplexity spend)
    raw = json.dumps({
        "company": company.strip(),
        "website": website.strip(),
        "persona": persona.strip(),
        "value_prop": value_prop.strip(),
        "initiative": initiative.strip(),
        "region": region.strip(),
        "competitor": competitor.strip(),
        "model": model,
        "week": iso_week_id(now_et()),
    }, sort_keys=True)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cache_key = k_cache(h)

    remaining_after = decrement_credit(email)

    with st.spinner("Researching with Perplexity..."):
        try:
            cached = db_get(cache_key)
            if cached:
                brief = cached
            else:
                brief = perplexity_brief(
                    company=company.strip(),
                    website=website.strip(),
                    persona=persona.strip(),
                    value_prop=value_prop.strip(),
                    initiative=initiative.strip(),
                    region=region.strip(),
                    competitor=competitor.strip(),
                    model=model,
                )
                db_set(cache_key, brief)

            run_record = {
                "ran_at": to_iso(now_et()),
                "company": company.strip(),
                "persona": persona.strip(),
                "model": model,
                "credits_remaining_after": remaining_after,
                "brief": brief,
            }
            save_history(email, run_record)

            st.success("Done.")

        except Exception as e:
            # Refund credit on failure
            update_user_credits(email, remaining_after + 1)
            st.error(f"Agent failed: {e}")
            return

    # Display result
    st.write("## Executive Brief (Proof-First)")

    st.json(brief)

    st.write("## Download 1-page PDF")
    try:
        pdf_bytes = brief_to_pdf_bytes(brief)
        st.download_button(
            label="Download PDF",
            data=pdf_bytes,
            file_name=f"{company.strip().replace(' ','_')}_brief.pdf",
            mime="application/pdf"
        )
    except Exception as e:
        st.warning(f"PDF generation failed: {e}")

    meta = brief.get("_api_meta", {})
    if meta:
        with st.expander("API transparency (usage/search results)"):
            st.json(meta)


def render_history():
    email = st.session_state["email"]
    st.subheader("History (last 25)")
    hist = get_history(email)
    if not hist:
        st.write("No runs yet.")
        return
    for item in hist:
        st.write("---")
        st.write(f"**{item.get('company','')}** — {item.get('persona','')} — {item.get('ran_at','')}")
        st.write(f"Model: {item.get('model','')} | Credits after: {item.get('credits_remaining_after','')}")
	### st.write (f"Credits after: {item.get('credits_remaining_after','')}"
        with st.expander("View brief JSON"):
            st.json(item.get("brief", {}))


def main():
    st.set_page_config(page_title=APP_NAME_DEFAULT, layout="wide")
    init_state()

    APP_NAME = optional_env("APP_NAME", APP_NAME_DEFAULT)
    st.title(APP_NAME)

    # Required secrets
    MAKE_WEBHOOK_SIGNUP_URL = must_env("MAKE_WEBHOOK_SIGNUP_URL")
    MAKE_WEBHOOK_CONTACT_URL = must_env("MAKE_WEBHOOK_CONTACT_URL")
    OWNER_EMAIL = must_env("OWNER_EMAIL")
    OWNER_ACCESS_CODE = must_env("OWNER_ACCESS_CODE")  # set to KBUILDSAI2026
    ADMIN_PASSWORD = must_env("ADMIN_PASSWORD")

    # Nav
    if st.session_state["logged_in"]:
        colA, colB = st.columns([3, 1])
        with colB:
            if st.button("Log out"):
                logout()
                st.rerun()

        tabs = ["Agent", "History"]
        if st.session_state.get("is_admin"):
            tabs.insert(0, "Admin")

        selected = st.tabs(tabs)

        idx = 0
        if st.session_state.get("is_admin"):
            with selected[idx]:
                render_admin(ADMIN_PASSWORD)
            idx += 1

        with selected[idx]:
            render_agent(MAKE_WEBHOOK_CONTACT_URL)
        with selected[idx + 1]:
            render_history()

    else:
        t1, t2 = st.tabs(["Request Access", "Log In"])
        with t1:
            render_request_access(MAKE_WEBHOOK_SIGNUP_URL)
        with t2:
            render_login(OWNER_EMAIL, OWNER_ACCESS_CODE)

        st.info("Owner/admin? Log in with your OWNER_EMAIL + OWNER_ACCESS_CODE to access Admin tools.")


if __name__ == "__main__":
    main()
