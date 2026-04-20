"""SQLite database layer for Emissary features (COA, Briefings, Exercises, Activity Log)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "emissary.db"


def _now() -> str:
    """Return current UTC datetime as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    """Return a short random hex id."""
    return uuid.uuid4().hex[:12]


def init_db() -> None:
    """Create all tables idempotently."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS coas (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                target_entities TEXT,
                action_type TEXT,
                status TEXT DEFAULT 'draft',
                confidence REAL,
                source_analysis_id TEXT,
                recommendations TEXT,
                friendly_fire TEXT,
                expected_effects TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                event_type TEXT,
                source TEXT DEFAULT 'system',
                message TEXT,
                severity TEXT DEFAULT 'info',
                related_id TEXT
            );

            CREATE TABLE IF NOT EXISTS briefings (
                id TEXT PRIMARY KEY,
                title TEXT,
                type TEXT,
                status TEXT DEFAULT 'draft',
                reference_id TEXT,
                content_markdown TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS exercises (
                id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT DEFAULT 'planning',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS injects (
                id TEXT PRIMARY KEY,
                exercise_id TEXT,
                inject_type TEXT,
                target_groups TEXT,
                content TEXT,
                scheduled_offset TEXT DEFAULT '00:00',
                urgency TEXT DEFAULT 'routine',
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                FOREIGN KEY (exercise_id) REFERENCES exercises(id)
            );

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                kind TEXT NOT NULL,
                username TEXT,
                feature TEXT,
                path TEXT,
                method TEXT,
                status_code INTEGER,
                latency_ms INTEGER,
                client_ip TEXT,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_usage_kind ON usage_events(kind);
            CREATE INDEX IF NOT EXISTS idx_usage_username ON usage_events(username);
            """
        )
        conn.commit()

        # Add assessment columns if they don't exist
        try:
            conn.execute("ALTER TABLE injects ADD COLUMN score REAL")
        except Exception:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE injects ADD COLUMN assessment_notes TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE exercises ADD COLUMN assessment_summary TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE exercises ADD COLUMN overall_score REAL")
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()


def seed_mock_data() -> None:
    """Populate the database with realistic demo data. Clears existing data first."""
    conn = get_db()
    try:
        # Clear all tables
        for table in ("injects", "exercises", "briefings", "activity_log", "coas"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

        now = _now()

        # --- COAs across all statuses ---
        coa_samples = [
            (_new_id(), "SWIFT Node Disconnection [Delta]",
             "Sever SWIFT access for Central State Bank to disrupt regime financing. Coordinate with EU partners for synchronized implementation.",
             '["Central State Bank", "Ministry of Finance"]', "asset_freeze", "executing", 0.92,
             '["Freeze all correspondent banking relationships with CSB", "Direct FinCEN to issue advisory on secondary sanctions risk", "Coordinate with FATF for grey-listing assessment"]',
             '[{"entity": "JP Morgan Chase", "details": "Correspondent banking exposure ~$340M annually", "risk_level": "MODERATE"}, {"entity": "Deutsche Bank AG", "details": "Trade finance pipeline for CSB petroleum transactions", "risk_level": "HIGH"}]',
             '["80% reduction in USD-denominated transactions within 30 days", "Regime forced to alternative payment channels (crypto, barter)", "Increased pressure on diplomatic negotiations"]',
             now, now),
            (_new_id(), "Phase 1 Semiconductor Embargo",
             "Restrict export of advanced lithography equipment and EDA software to designated PRC entities under BIS Entity List expansion.",
             '["SMIC", "Huawei HiSilicon", "YMTC"]', "export_control", "draft", 0.45,
             '["Add 14 PRC semiconductor entities to BIS Entity List", "Coordinate with Netherlands and Japan for parallel ASML/TEL restrictions", "Establish end-use monitoring for legacy node equipment"]',
             '[{"entity": "Qualcomm", "details": "Revenue loss from restricted chip sales ~$8B annually", "risk_level": "HIGH"}, {"entity": "Applied Materials", "details": "Service contract disruption for existing PRC installations", "risk_level": "MODERATE"}]',
             '["Delay PRC 7nm-equivalent production by 18-24 months", "Accelerate indigenous PRC chip development programs", "Potential retaliation against US rare earth supply"]',
             now, now),
            (_new_id(), "Maritime Lane Asset Freeze",
             "Freeze assets of Eastern Shipping Conglomerate entities identified in sanctions evasion network operating through Strait of Malacca.",
             '["Eastern Shipping Conglomerate", "Oceanic Holdings Ltd", "Pacific Maritime Services"]', "sanction", "under_review", 0.68,
             '["Designate ESC and 7 subsidiaries under EO 13846", "Issue OFAC advisory on ship-to-ship transfer risks in Malacca Strait", "Coordinate with Singapore MAS for parallel financial restrictions"]',
             '[{"entity": "Maersk Line", "details": "Shared port facilities in Singapore may face operational delays", "risk_level": "LOW"}, {"entity": "US Pacific Fleet", "details": "Increased maritime patrol requirements in SCS", "risk_level": "MODERATE"}]',
             '["Disrupt 60% of sanctioned oil shipment volume within 90 days", "Force rerouting through longer Cape of Good Hope route", "Increased insurance premiums for vessels in region"]',
             now, now),
            (_new_id(), "Lithium Supply Chain Restriction",
             "Implement investment screening for PRC-linked lithium mining acquisitions in Chile, Argentina, and Australia under CFIUS expanded jurisdiction.",
             '["Ganfeng Lithium", "Tianqi Lithium", "CATL"]', "investment_screening", "draft", 0.81,
             '["Invoke CFIUS jurisdiction for lithium assets under critical minerals EO", "Coordinate with Australian FIRB and Chilean CODELCO", "Establish allied critical minerals purchasing consortium"]',
             '[]',
             '["Block 3 pending PRC lithium acquisitions worth $4.2B", "Secure allied access to 40% of global lithium reserves", "PRC forced to accelerate sodium-ion battery alternatives"]',
             now, now),
            (_new_id(), "Cyber Infrastructure Sanctions",
             "Designate PRC cyber units and affiliated technology companies enabling cyber operations against allied critical infrastructure.",
             '["APT41 Front Companies", "PRC MSS Cyber Bureau", "Integrity Tech"]', "sanction", "approved", 0.73,
             '["Designate 12 entities under EO 13694 (cyber sanctions)", "Coordinate Five Eyes joint attribution statement", "Direct CISA to issue binding operational directive for affected sectors"]',
             '[{"entity": "Cisco Systems", "details": "Hardware already deployed in designated entity networks", "risk_level": "LOW"}]',
             '["Disrupt PRC cyber operational infrastructure in 60% of identified nodes", "Deter future cyber operations through visible attribution costs", "Strengthen allied cyber defense coordination"]',
             now, now),
        ]
        for s in coa_samples:
            conn.execute(
                "INSERT INTO coas (id, name, description, target_entities, action_type, status, confidence, recommendations, friendly_fire, expected_effects, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                s,
            )

        # --- Briefings ---
        briefing_samples = [
            (_new_id(), "South China Sea Transit Analysis", "coa_brief", "finalized", coa_samples[2][0],
             """# South China Sea Transit Analysis

## I. Situation

Satellite imagery and AIS transponder data indicate an unprecedented accumulation of commercial maritime traffic within the eastern approaches of the Strait of Malacca. Current vessel count exceeds seasonal averages by 42%. Weather conditions are deteriorating with visibility reduced to < 2nm due to regional haze.

Intelligence confirms that Eastern Shipping Conglomerate (ESC) continues to operate a network of 7 subsidiary shell companies facilitating ship-to-ship transfers of sanctioned petroleum products. Three vessels flagged under Togolese registry have been observed conducting dark operations (AIS transponders disabled) within designated transit lanes.

## II. Analysis

The bottleneck is attributed to a combination of increased PRC naval exercises in the South China Sea and the presence of ESC "dark fleet" vessels deliberately congesting high-traffic corridors. This represents a gray-zone tactic designed to stress regional logistics nodes while providing plausible deniability.

Economic impact assessment indicates potential disruption to $2.4B in daily trade flows through the Strait. US-allied shipping interests account for approximately 34% of affected traffic. Singapore port operations report a 28% increase in vessel waiting times, with cascading effects on just-in-time supply chains serving US military installations in the region.

Secondary analysis suggests this activity is coordinated with broader PRC economic coercion campaigns targeting ASEAN nations that supported recent UN maritime dispute resolutions.

## III. Recommendation

Immediate implementation of COA "Maritime Lane Asset Freeze" targeting ESC and identified subsidiaries. Specific actions:

1. **OFAC Designation**: Designate ESC and 7 subsidiaries under EO 13846 within 72 hours
2. **Maritime Enforcement**: Deploy Task Force Sentinel assets to provide escort for priority US-flagged vessels and conduct boarding operations on suspected sanctions-evading tankers
3. **Allied Coordination**: Coordinate with Singapore MAS and Malaysian MMEA for parallel port access restrictions
4. **Intelligence Collection**: Redirect NRO ISR assets to maintain persistent coverage of identified dark fleet operating areas
5. **Diplomatic Channel**: Initiate bilateral discussions with Togo regarding flag registry abuse

## IV. Risk Assessment

**Friendly Fire**: Maersk Line operates shared port facilities in Singapore that may experience 48-72 hour operational delays during initial enforcement phase. US Pacific Fleet patrol tempo will increase by approximately 15%, requiring reallocation from other AOR commitments.

**Escalation Risk**: MODERATE. PRC may interpret enforcement actions as provocation in contested waters. Recommend maintaining diplomatic back-channels through Singapore and limiting enforcement to international waters.

**Confidence**: HIGH (0.85) — based on corroborated SIGINT, AIS data, and HUMINT from regional maritime authorities.

---
*EMISSARY DEMO — NOT AN OFFICIAL DOCUMENT*""",
             now, now),
            (_new_id(), "Strait of Malacca Congestion Assessment", "bda_report", "reviewing", None,
             """# Strait of Malacca Congestion — Battle Damage Assessment

## I. Situation

Following implementation of maritime enforcement operations and OFAC designations against Eastern Shipping Conglomerate, this BDA evaluates the effectiveness of actions taken over the preceding 14-day period.

## II. Analysis

**Positive Indicators:**
- ESC vessel movements reduced by 64% in target operating area
- Ship-to-ship transfer incidents decreased from 12/week to 3/week
- Two ESC subsidiaries (Oceanic Holdings Ltd, Pacific Maritime Services) have ceased operations
- Singapore-flagged vessels report 18% improvement in transit times

**Negative Indicators:**
- Three ESC vessels re-flagged under Comoros registry and resumed operations
- New shell company "Indo-Pacific Logistics Group" identified, likely ESC successor entity
- Sanctioned petroleum volumes partially redirected through Sunda Strait alternative route
- Insurance premiums for commercial vessels in region increased 12%, affecting allied shipping costs

**Net Assessment:**
Actions achieved approximately 60% reduction in target activity, consistent with pre-action projections. However, adaptation timeline was faster than anticipated (7 days vs. projected 21 days), suggesting pre-positioned contingency planning by ESC network.

## III. Recommendation

1. Expand OFAC designations to include identified successor entities
2. Coordinate with Comoros maritime authority for flag registry review
3. Extend ISR coverage to Sunda Strait to track rerouted traffic
4. Consider secondary sanctions framework for insurance providers servicing re-flagged vessels

## IV. Risk Assessment

Continued enforcement will require sustained naval presence. Risk of normalization — prolonged operations may reduce deterrent effect. Recommend 90-day reassessment window.

---
*EMISSARY DEMO — NOT AN OFFICIAL DOCUMENT*""",
             now, now),
            (_new_id(), "Semiconductor Supply Chain Vulnerability Brief", "situation_update", "finalized", coa_samples[1][0],
             """# Semiconductor Supply Chain — Situation Update

## I. Situation

Following the proposed Phase 1 Semiconductor Embargo targeting SMIC, Huawei HiSilicon, and YMTC, this briefing assesses the current state of PRC semiconductor self-sufficiency efforts and the potential impact of expanded export controls.

Recent intelligence indicates SMIC has achieved limited 7nm-equivalent production capability using modified DUV lithography processes, circumventing restrictions on EUV equipment access. Production yield remains below 30%, compared to TSMC's 90%+ yields at equivalent nodes.

## II. Analysis

PRC semiconductor investment reached $48.2B in 2025, a 34% increase over 2024. The "Big Fund III" has allocated $27.5B specifically for equipment localization, targeting domestic alternatives to ASML, Applied Materials, and Lam Research products.

Key vulnerabilities identified:
- **EDA Software**: PRC domestic alternatives (Empyrean, Primarius) cover only 40% of required design tool functionality
- **Photoresist**: 95% dependency on Japanese suppliers (JSR, TOK, Shin-Etsu)
- **Advanced Packaging**: JCET Group capacity insufficient for military-grade chip packaging at scale

Embargo effectiveness is projected at 18-24 month delay to PRC 5nm-equivalent capability, with significant uncertainty due to clandestine procurement channels identified operating through Southeast Asian intermediaries.

## III. Recommendation

Proceed with Phase 1 Semiconductor Embargo while strengthening multilateral coordination with Japan and Netherlands. Critical supplementary actions include enhanced end-use monitoring and expansion of Denied Persons List to cover identified procurement networks.

---
*EMISSARY DEMO — NOT AN OFFICIAL DOCUMENT*""",
             now, now),
            (_new_id(), "Carrier Group 5 Fuel Logistics", "bda_report", "draft", None,
             """# Carrier Group 5 Fuel Logistics Assessment

## I. Situation

Routine assessment of fuel logistics chain supporting CVN-72 Abraham Lincoln Carrier Strike Group operations in the Western Pacific. Current operational tempo requires 3.2M gallons of JP-5 per week across the strike group.

## II. Analysis

Primary fuel supply via Military Sealift Command oilers T-AO 205 (USNS John Lewis) and T-AO 195 (USNS Leroy Grumman). Current fuel reserves at Yokosuka and Sasebo are at 78% capacity, with resupply shipments on standard 14-day cycle.

Potential disruption scenarios under current exercise conditions:
- Malacca Strait congestion adds 4-6 day transit delay for commercial fuel tankers
- Alternative routing via Lombok Strait increases transit cost by approximately $180K per shipment
- Regional fuel bunkering costs have increased 8% due to insurance premium increases

No immediate logistics threat identified, but sustained operations at current tempo will draw reserves to 60% within 21 days if commercial resupply is disrupted.

## III. Recommendation

Pre-position additional fuel stocks at Diego Garcia as contingency reserve. Coordinate with Republic of Korea Navy for emergency bunkering agreements at Busan.

---
*EMISSARY DEMO — NOT AN OFFICIAL DOCUMENT*""",
             now, now),
        ]
        for b in briefing_samples:
            conn.execute(
                "INSERT INTO briefings (id, title, type, status, reference_id, content_markdown, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                b,
            )

        # --- Exercise + Injects ---
        exercise_id = _new_id()
        conn.execute(
            "INSERT INTO exercises (id, name, status, created_at) VALUES (?, ?, ?, ?)",
            (exercise_id, "Global Sentinel 24", "active", now),
        )
        inject_samples = [
            (_new_id(), exercise_id, "Intelligence Report", '["INDOPACOM"]',
             "SIGINT intercept confirms PRC Navy South Sea Fleet conducting unscheduled live-fire exercises in disputed waters near Scarborough Shoal. Assess high probability of escalatory signaling.",
             "08:00", "HIGH", "delivered", now),
            (_new_id(), exercise_id, "Physical Threat Event", '["INDOPACOM", "CYBERCOM"]',
             "Dark fleet tanker disabled in main transit lane of Strait of Malacca. AIS data suggests deliberate positioning. Regional maritime traffic diverted.",
             "10:30", "HIGH", "delivered", now),
            (_new_id(), exercise_id, "Diplomatic Cable", '["STATE DEPT"]',
             "PRC Foreign Ministry issues formal demarche regarding US naval presence in Taiwan Strait. Requests immediate withdrawal of DDG assets within 72 hours.",
             "16:45", "MED", "pending", now),
            (_new_id(), exercise_id, "Infrastructure Failure", '["CYBERCOM", "INDOPACOM"]',
             "Port of Singapore automated scheduling system experiencing intermittent failures consistent with cyber intrusion. Vessel berthing delays accumulating.",
             "20:00", "HIGH", "pending", now),
            (_new_id(), exercise_id, "Social Media Misinfo", '["STATE DEPT", "ALL"]',
             "Coordinated social media campaign detected across Weibo and Telegram channels claiming US Navy vessel collision in South China Sea. No incident confirmed. Narrative gaining traction in regional media.",
             "23:30", "LOW", "pending", now),
        ]
        for inj in inject_samples:
            conn.execute(
                "INSERT INTO injects (id, exercise_id, inject_type, target_groups, content, scheduled_offset, urgency, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                inj,
            )

        # --- Activity Log entries ---
        activity_entries = [
            (now, "system_startup", "system", "Emissary platform initialized with mock data", "info", None),
            (now, "exercise_created", "system", "Exercise 'Global Sentinel 24' created", "info", exercise_id),
            (now, "exercise_status_changed", "system", "Exercise status changed to active", "info", exercise_id),
        ]
        for s in coa_samples:
            activity_entries.append((now, "coa_created", "system", f"COA '{s[1]}' created", "info", s[0]))
        for b in briefing_samples:
            activity_entries.append((now, "briefing_created", "system", f"Briefing '{b[1]}' created", "info", b[0]))
        for inj in inject_samples[:2]:  # Only the delivered ones
            activity_entries.append((now, "inject_created", "system", f"Inject '{inj[2]}' added at T+{inj[5]}", "info", inj[0]))
            activity_entries.append((now, "inject_delivered", "system", f"Inject '{inj[2]}' delivered to {inj[3]}", "info", inj[0]))
        activity_entries.append((now, "alert", "monitor", "Participant deviation detected in Sector 4", "warning", None))
        activity_entries.append((now, "alert", "monitor", "Critical threshold exceeded on maritime traffic volume near Malacca Strait", "error", None))

        for a in activity_entries:
            conn.execute(
                "INSERT INTO activity_log (timestamp, event_type, source, message, severity, related_id) VALUES (?, ?, ?, ?, ?, ?)",
                a,
            )

        conn.commit()
        print(f"Mock data seeded: {len(coa_samples)} COAs, {len(briefing_samples)} briefings, 1 exercise, {len(inject_samples)} injects, {len(activity_entries)} activity entries")
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with Row factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def log_activity(
    event_type: str,
    message: str,
    source: str = "system",
    severity: str = "info",
    related_id: str | None = None,
) -> None:
    """Insert a row into activity_log."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO activity_log (timestamp, event_type, source, message, severity, related_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), event_type, source, message, severity, related_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_usage_event(
    kind: str,
    username: str | None = None,
    feature: str | None = None,
    path: str | None = None,
    method: str | None = None,
    status_code: int | None = None,
    latency_ms: int | None = None,
    client_ip: str | None = None,
    detail: str | None = None,
) -> None:
    """Insert a row into usage_events. Best-effort: swallows DB errors so logging never breaks a request."""
    try:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO usage_events "
                "(timestamp, kind, username, feature, path, method, status_code, latency_ms, client_ip, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), kind, username, feature, path, method, status_code, latency_ms, client_ip, detail),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def query_usage_summary(days: int = 30) -> dict:
    """Return aggregate usage stats for the last N days."""
    conn = get_db()
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logins_per_day = [
            dict(row) for row in conn.execute(
                "SELECT substr(timestamp, 1, 10) AS day, "
                "       SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) AS success, "
                "       SUM(CASE WHEN status_code != 200 THEN 1 ELSE 0 END) AS failure, "
                "       COUNT(DISTINCT username) AS unique_users "
                "FROM usage_events "
                "WHERE kind = 'login_attempt' AND timestamp >= ? "
                "GROUP BY day ORDER BY day",
                (cutoff_iso,),
            ).fetchall()
        ]

        top_features = [
            dict(row) for row in conn.execute(
                "SELECT feature, COUNT(*) AS hits, COUNT(DISTINCT username) AS unique_users "
                "FROM usage_events "
                "WHERE kind = 'api_request' AND feature IS NOT NULL AND timestamp >= ? "
                "GROUP BY feature ORDER BY hits DESC LIMIT 20",
                (cutoff_iso,),
            ).fetchall()
        ]

        top_users = [
            dict(row) for row in conn.execute(
                "SELECT username, COUNT(*) AS events, MAX(timestamp) AS last_seen "
                "FROM usage_events "
                "WHERE username IS NOT NULL AND timestamp >= ? "
                "GROUP BY username ORDER BY events DESC LIMIT 20",
                (cutoff_iso,),
            ).fetchall()
        ]

        recent_logins = [
            dict(row) for row in conn.execute(
                "SELECT timestamp, username, status_code, client_ip, detail "
                "FROM usage_events "
                "WHERE kind = 'login_attempt' "
                "ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()
        ]

        top_endpoints = [
            dict(row) for row in conn.execute(
                "SELECT feature, method, path, COUNT(*) AS hits, COUNT(DISTINCT username) AS unique_users "
                "FROM usage_events "
                "WHERE kind = 'api_request' AND path IS NOT NULL AND timestamp >= ? "
                "GROUP BY feature, method, path ORDER BY hits DESC LIMIT 50",
                (cutoff_iso,),
            ).fetchall()
        ]

        return {
            "window_days": days,
            "logins_per_day": logins_per_day,
            "top_features": top_features,
            "top_endpoints": top_endpoints,
            "top_users": top_users,
            "recent_logins": recent_logins,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Row converters – sqlite3.Row -> dict, deserialising JSON text columns
# ---------------------------------------------------------------------------

def _json_field(row: sqlite3.Row, key: str, default=None):
    """Safely parse a JSON text column."""
    val = row[key]
    if val is None:
        return default if default is not None else []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def row_to_coa(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "target_entities": _json_field(row, "target_entities", []),
        "action_type": row["action_type"],
        "status": row["status"],
        "confidence": row["confidence"],
        "source_analysis_id": row["source_analysis_id"],
        "recommendations": _json_field(row, "recommendations", []),
        "friendly_fire": _json_field(row, "friendly_fire", []),
        "expected_effects": _json_field(row, "expected_effects", []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_inject(row: sqlite3.Row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "exercise_id": row["exercise_id"],
        "inject_type": row["inject_type"],
        "target_groups": _json_field(row, "target_groups", []),
        "content": row["content"],
        "scheduled_offset": row["scheduled_offset"],
        "urgency": row["urgency"],
        "status": row["status"],
        "created_at": row["created_at"],
        "score": row["score"] if "score" in keys else None,
        "assessment_notes": row["assessment_notes"] if "assessment_notes" in keys else "",
    }


def row_to_activity(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "event_type": row["event_type"],
        "source": row["source"],
        "message": row["message"],
        "severity": row["severity"],
        "related_id": row["related_id"],
    }


def row_to_briefing(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "type": row["type"],
        "status": row["status"],
        "reference_id": row["reference_id"],
        "content_markdown": row["content_markdown"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_exercise(row: sqlite3.Row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "assessment_summary": row["assessment_summary"] if "assessment_summary" in keys else "",
        "overall_score": row["overall_score"] if "overall_score" in keys else None,
    }
