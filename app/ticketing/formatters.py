"""
Centralized Ticket Content Formatters for Frappe Helpdesk and ERPNext.
Generates structured, clean, professional HTML descriptions and timeline comments.
"""

import html
import re
from typing import Dict, Any, Optional, List


def format_ticket_description(
    caller_info: Optional[Dict[str, Any]] = None,
    issue_category: str = "IT Support",
    priority: str = "Medium",
    call_outcome: str = "Open (Unassigned)",
    summary_of_issue: str = "IT Support Inquiry",
    impact: Optional[str] = None,
    key_details: Optional[List[Dict[str, str]]] = None,
    troubleshooting_steps: Optional[List[str]] = None,
    ai_resolution_note: Optional[str] = None,
    requires_approval: bool = False,
    is_emergency: bool = False,
) -> str:
    """
    Build a neat, uniform executive summary card for Frappe Helpdesk ticket description.
    Focuses strictly on actionable information for the IT technician:
    - User Affected
    - Issue & Severity
    - Summary of Issue & Impact
    - What AI Agent (Arif) Has Done
    """
    caller_info = caller_info or {}
    caller_name = html.escape(caller_info.get("name") or caller_info.get("verified_name") or "Direct Caller")
    employee_id = html.escape(str(caller_info.get("employee_id") or "Unverified"))
    department = html.escape(caller_info.get("department") or "General")
    phone = html.escape(caller_info.get("phone") or "N/A")
    raw_tier = caller_info.get("tier") or "STANDARD"
    tier = html.escape(raw_tier)

    # Tier badge styling
    if "VIP" in raw_tier or "EXECUTIVE" in raw_tier:
        tier_badge = f'<span style="background: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #fde68a;">⭐ {tier}</span>'
    else:
        tier_badge = f'<span style="background: #f1f5f9; color: #475569; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;">{tier}</span>'

    safe_category = html.escape(issue_category or "IT Support")
    safe_priority = html.escape(priority or "Medium")
    safe_outcome = html.escape(call_outcome or "Logged")
    safe_summary = html.escape(summary_of_issue or "IT Support Inquiry")
    safe_impact = html.escape(impact) if impact else None

    # Priority badge styling
    p_lower = safe_priority.lower()
    if any(k in p_lower for k in ["urgent", "4", "emergency", "critical", "p1"]):
        priority_badge = f'<span style="background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #fca5a5;">🚨 {safe_priority}</span>'
    elif any(k in p_lower for k in ["high", "3", "p2"]):
        priority_badge = f'<span style="background: #ffedd5; color: #c2410c; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid #fed7aa;">⚠️ {safe_priority}</span>'
    else:
        priority_badge = f'<span style="background: #e0f2fe; color: #0369a1; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;">{safe_priority}</span>'

    html_parts = [
        '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1e293b; max-width: 800px;">'
    ]

    # Banner 1: Emergency Sev-1 Banner
    if is_emergency:
        html_parts.append(
            '<div style="background: #fef2f2; border: 1px solid #f87171; border-left: 5px solid #dc2626; border-radius: 6px; padding: 12px 16px; margin-bottom: 14px;">'
            '<div style="color: #991b1b; font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 6px;">'
            '🚨 <span>SEV-1 CRITICAL OUTAGE ESCALATION</span>'
            '</div>'
            '<div style="color: #b91c1c; font-size: 12px; margin-top: 4px;">'
            'This incident was escalated immediately to emergency on-call engineering. High-priority resolution required.'
            '</div>'
            '</div>'
        )

    # Banner 2: Hardware Approval Notice
    if requires_approval:
        html_parts.append(
            '<div style="background: #fffbeb; border: 1px solid #fcd34d; border-left: 5px solid #d97706; border-radius: 6px; padding: 12px 16px; margin-bottom: 14px;">'
            '<div style="color: #92400e; font-weight: 700; font-size: 13px;">'
            '⚠️ <span>ACTION REQUIRED: DEPARTMENT MANAGER APPROVAL</span>'
            '</div>'
            '<div style="color: #b45309; font-size: 12px; margin-top: 4px;">'
            f'Per National Finance IT governance, physical equipment dispatch requires Department Manager approval. Caller Department: <strong>{department}</strong>.'
            '</div>'
            '</div>'
        )

    # Section 1: User Affected
    html_parts.append(
        '<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;">'
        '<div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #0284c7; margin-bottom: 8px;">'
        '👤 User Affected'
        '</div>'
        '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
        f'<tr><td style="padding: 3px 0; width: 22%; color: #64748b; font-weight: 600;">Full Name:</td><td style="padding: 3px 0; width: 78%; color: #0f172a; font-weight: 600;">{caller_name}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">Employee ID:</td><td style="padding: 3px 0; color: #0f172a;">{employee_id}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">Department:</td><td style="padding: 3px 0; color: #0f172a;">{department}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">Contact Phone:</td><td style="padding: 3px 0; color: #0f172a;">{phone}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">User Tier:</td><td style="padding: 3px 0;">{tier_badge}</td></tr>'
        '</table>'
        '</div>'
    )

    # Section 2: Issue & Severity
    html_parts.append(
        '<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;">'
        '<div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #0284c7; margin-bottom: 8px;">'
        '⚠️ Issue & Severity'
        '</div>'
        '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
        f'<tr><td style="padding: 3px 0; width: 22%; color: #64748b; font-weight: 600;">Category:</td><td style="padding: 3px 0; width: 78%; color: #0f172a;">{safe_category}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">Priority:</td><td style="padding: 3px 0;">{priority_badge}</td></tr>'
        f'<tr><td style="padding: 3px 0; color: #64748b; font-weight: 600;">Call Outcome:</td><td style="padding: 3px 0; color: #0f172a; font-weight: 600;">{safe_outcome}</td></tr>'
        '</table>'
        '</div>'
    )

    # Section 3: Summary of Issue
    details_html = ""
    if key_details:
        cleaned_details = []
        for item in key_details:
            fld = html.escape(str(item.get("field", "")).strip())
            val = html.escape(str(item.get("value", "")).strip())
            if fld and val and fld.lower() not in ["issue_description", "summary"]:
                cleaned_details.append(f'<li><strong>{fld.replace("_", " ").title()}:</strong> {val}</li>')
        if cleaned_details:
            details_html = f'<ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 12px; color: #334155;">{"".join(cleaned_details)}</ul>'

    impact_html = f'<div style="margin-top: 6px; font-size: 12px; color: #b91c1c; font-weight: 600;">⚠️ Impact: {safe_impact}</div>' if safe_impact else ""

    html_parts.append(
        '<div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;">'
        '<div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #0284c7; margin-bottom: 8px;">'
        '📝 Summary of Issue'
        '</div>'
        f'<div style="font-size: 13px; color: #0f172a; font-weight: 500; line-height: 1.5;">{safe_summary}</div>'
        f'{impact_html}'
        f'{details_html}'
        '</div>'
    )

    # Section 4: What AI Agent Has Done
    actions_items = []
    # 1. Verification status
    if caller_info.get("employee_id"):
        actions_items.append(f'<li>Identity verified via Employee ID: <strong>{employee_id}</strong>.</li>')
    else:
        actions_items.append('<li>Identity: Unverified caller.</li>')

    # 2. Troubleshooting steps attempted
    if troubleshooting_steps:
        for step in troubleshooting_steps:
            safe_step = html.escape(str(step).strip())
            if safe_step and not safe_step.startswith("issue_description:"):
                # Clean prefix if it looks like field: value
                actions_items.append(f'<li>{safe_step}</li>')

    # 3. Resolution / Action note
    if ai_resolution_note:
        safe_res = html.escape(str(ai_resolution_note).strip())
        actions_items.append(f'<li style="font-weight: 600; color: #0f172a;">{safe_res}</li>')
    elif "Resolved" in safe_outcome:
        actions_items.append('<li style="font-weight: 600; color: #15803d;">First-contact resolution achieved on call by AI Agent Arif.</li>')
    elif "Transfer" in safe_outcome:
        actions_items.append('<li style="font-weight: 600; color: #0284c7;">Call transferred to human IT support queue for specialized assistance.</li>')

    html_parts.append(
        '<div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 16px;">'
        '<div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #16a34a; margin-bottom: 8px;">'
        '🤖 What AI Agent (Arif) Has Done'
        '</div>'
        f'<ul style="margin: 0; padding-left: 20px; font-size: 13px; color: #166534; line-height: 1.5;">'
        f'{"".join(actions_items)}'
        '</ul>'
        '</div>'
    )

    html_parts.append('</div>')
    return "".join(html_parts)


def format_ticket_comment(
    call_id: str,
    caller_phone: str = "N/A",
    duration: Optional[str] = None,
    language: str = "English",
    call_outcome: str = "Completed",
    transfer_target: Optional[str] = None,
    transcript_lines: Optional[List[str]] = None,
) -> str:
    """
    Build a clean, structured timeline comment for Frappe Helpdesk (HD Ticket Comment).
    Includes:
    - Call Audit Record (Call ID, Timestamp, Phone, Language, Outcome)
    - Full Clean Conversation Dialogue Timeline
    """
    safe_call_id = html.escape(str(call_id or "N/A"))
    safe_phone = html.escape(str(caller_phone or "N/A"))
    safe_duration = html.escape(str(duration or "N/A"))
    safe_language = html.escape(str(language or "English"))
    safe_outcome = html.escape(str(call_outcome or "Completed"))
    safe_target = html.escape(str(transfer_target or "N/A"))

    dialogue_html = []
    if transcript_lines:
        for line in transcript_lines:
            line_str = str(line).strip()
            if not line_str:
                continue

            # Check if line matches "[timestamp] Speaker: message"
            match = re.match(r"^(\[[0-9:]+\]\s*)?([A-Za-z0-9_\s\u0600-\u06FF]+):\s*(.*)$", line_str)
            if match:
                ts = html.escape(match.group(1) or "")
                speaker = html.escape(match.group(2).strip())
                msg = html.escape(match.group(3).strip())

                if "arif" in speaker.lower():
                    badge = f'<span style="color: #0284c7; font-weight: 700;">{ts}{speaker}:</span>'
                else:
                    badge = f'<span style="color: #10b981; font-weight: 700;">{ts}{speaker}:</span>'
                dialogue_html.append(f'<div style="margin-bottom: 6px; line-height: 1.4;">{badge} <span>{msg}</span></div>')
            else:
                dialogue_html.append(f'<div style="margin-bottom: 6px; line-height: 1.4; color: #64748b;">{html.escape(line_str)}</div>')

    transcript_content = "".join(dialogue_html) if dialogue_html else '<div style="color: #94a3b8; font-style: italic;">No transcript recorded on this call.</div>'

    html_block = (
        '<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; font-size: 13px; color: #1e293b;">'
        '<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px;">'
        '<div style="font-weight: 700; color: #0f172a; font-size: 13px; margin-bottom: 6px;">📞 Call Audit & Telephony Record</div>'
        '<div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px; font-size: 12px; color: #475569;">'
        f'<div><strong>Call ID:</strong> <code>{safe_call_id}</code></div>'
        f'<div><strong>Caller Phone:</strong> {safe_phone}</div>'
        f'<div><strong>Language:</strong> {safe_language}</div>'
        f'<div><strong>Outcome:</strong> {safe_outcome}</div>'
        + (f'<div><strong>Transfer Queue:</strong> {safe_target}</div>' if transfer_target else '')
        + (f'<div><strong>Duration:</strong> {safe_duration}</div>' if duration else '')
        + '</div>'
        '</div>'
        '<div style="font-weight: 700; color: #334155; font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;">💬 Conversation Transcript</div>'
        f'<div style="background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 12px 14px; font-size: 12px; max-height: 400px; overflow-y: auto;">'
        f'{transcript_content}'
        '</div>'
        '</div>'
    )
    return html_block
