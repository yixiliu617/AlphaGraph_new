from enum import Enum

class TenantTier(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"

class SourceType(str, Enum):
    SEC_FILING = "sec_filing"
    BROKER_REPORT = "broker_report"
    USER_NOTE = "user_note"
    TRANSCRIPT = "transcript"
    NEWS = "news"
    MEETING_NOTE = "meeting_note"    # Topic-level fragment extracted from meeting transcript
    MEETING_DELTA = "meeting_delta"  # Confirmed change between two meetings on the same topic

class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"

class CatalystStatus(str, Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    BROKEN = "broken"

class AssetClass(str, Enum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    COMMODITY = "commodity"
    CRYPTO = "crypto"

# ---------------------------------------------------------------------------
# Meeting Notes
# ---------------------------------------------------------------------------

class NoteType(str, Enum):
    EARNINGS_CALL = "earnings_call"
    MANAGEMENT_MEETING = "management_meeting"
    CONFERENCE = "conference"
    INTERNAL = "internal"

class SummaryStatus(str, Enum):
    NONE = "none"                       # No recording / no summary requested
    AWAITING_SPEAKERS = "awaiting_speakers"
    AWAITING_TOPICS = "awaiting_topics"
    EXTRACTING = "extracting"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"

class RecordingMode(str, Enum):
    WASAPI = "wasapi"        # Server captures system audio via WASAPI loopback
    BROWSER = "browser"      # Client sends audio chunks via WebSocket (mic fallback)
