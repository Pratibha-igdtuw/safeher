"""
SafeHer — Tier 3 Part 1: Input validation layer.

A small marshmallow-based validation layer used at the top of every
POST/PUT route instead of calling `request.get_json(force=True)` directly.

Usage in app.py:

    from validators import validate_json, LoginSchema

    @app.route("/login", methods=["POST"])
    @validate_json(LoginSchema)
    def login():
        data = g.validated_data   # already validated + type-coerced
        ...

On bad/missing JSON body -> 400 {"error": "invalid_json", ...}
On schema validation failure -> 400 {"error": "validation_failed", "fields": {...}}
"""

from functools import wraps

from flask import request, jsonify, g
from marshmallow import Schema, fields, validate, ValidationError, EXCLUDE


# ---------------------------------------------------------------------------
# Shared field building blocks
# ---------------------------------------------------------------------------
LATITUDE = fields.Float(validate=validate.Range(min=-90, max=90))
LONGITUDE = fields.Float(validate=validate.Range(min=-180, max=180))

# Nullable variants — several existing endpoints tolerate missing/None
# lat/lng (e.g. a checkin timeout has no location). allow_none lets the
# field be explicitly null without failing validation; still range-checked
# when a numeric value IS supplied.
LATITUDE_OPT = fields.Float(validate=validate.Range(min=-90, max=90), allow_none=True, load_default=None)
LONGITUDE_OPT = fields.Float(validate=validate.Range(min=-180, max=180), allow_none=True, load_default=None)


def _short_text(max_len):
    return fields.Str(validate=validate.Length(max=max_len))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    email = fields.Email(required=True)
    password = fields.Str(required=True, validate=validate.Length(min=1, max=256))


class SignupSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    email = fields.Email(required=True)
    password = fields.Str(required=True, validate=validate.Length(min=8, max=256))


class TOTPVerifySchema(Schema):
    """Ready for the 2FA verify-login endpoint referenced in the Tier 3
    spec. NOTE: no /api/2fa/verify-login route exists in this codebase yet
    (see README/PR notes) — this schema is here so it can be wired up as
    soon as that endpoint is added, without another validation pass."""

    class Meta:
        unknown = EXCLUDE

    email = fields.Email(required=True)
    code = fields.Str(required=True, validate=validate.Regexp(r"^\d{6}$", error="TOTP code must be exactly 6 digits"))


# ---------------------------------------------------------------------------
# Contacts / linked contacts
# ---------------------------------------------------------------------------
class ContactSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.Str(required=True, validate=validate.Length(min=1, max=120))
    phone = fields.Str(required=True, validate=validate.Length(min=1, max=30))
    relation = fields.Str(load_default="", validate=validate.Length(max=60))


class ContactInviteSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    email = fields.Email(required=True)


# ---------------------------------------------------------------------------
# SOS / distress / audits / feed / route safety / checkin
# ---------------------------------------------------------------------------
class SOSSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    latitude = LATITUDE_OPT
    longitude = LONGITUDE_OPT
    trigger_type = fields.Str(load_default="manual", validate=validate.Length(max=40))


class LocationSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    latitude = LATITUDE_OPT
    longitude = LONGITUDE_OPT


class DistressCheckSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    transcript = fields.Str(load_default="", validate=validate.Length(max=4000))
    location = fields.Nested(LocationSchema, load_default=dict)


class RouteSafetySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    origin = fields.Str(load_default="", validate=validate.Length(max=300))
    destination = fields.Str(load_default="", validate=validate.Length(max=300))


class CheckinStartSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    minutes = fields.Int(load_default=15, validate=validate.Range(min=1, max=1440))


class AuditSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    latitude = LATITUDE
    longitude = LONGITUDE
    area_name = _short_text(150)
    comment = _short_text(2000)
    lighting = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))
    openness = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))
    walkpath = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))
    security = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))
    transport = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))
    crowd = fields.Int(load_default=2, validate=validate.Range(min=0, max=4))


class CheckLocationRiskSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    latitude = LATITUDE
    longitude = LONGITUDE


class GuardianShareSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    latitude = LATITUDE_OPT
    longitude = LONGITUDE_OPT


class FeedPostSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    message = fields.Str(required=True, validate=validate.Length(min=1, max=2000))
    post_type = fields.Str(
        load_default="alert",
        validate=validate.OneOf(["alert", "safe_spot", "incident"]),
    )
    area_name = _short_text(150)
    latitude = LATITUDE_OPT
    longitude = LONGITUDE_OPT


# ---------------------------------------------------------------------------
# TIER 3 PART 3: Web Push + client-side error reporting
# ---------------------------------------------------------------------------
class PushKeysSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    p256dh = fields.Str(required=True, validate=validate.Length(min=1, max=500))
    auth = fields.Str(required=True, validate=validate.Length(min=1, max=500))


class PushSubscribeSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    endpoint = fields.Str(required=True, validate=validate.Length(min=1, max=2000))
    keys = fields.Nested(PushKeysSchema, required=True)


class PushUnsubscribeSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    endpoint = fields.Str(required=True, validate=validate.Length(min=1, max=2000))


class ClientErrorSchema(Schema):
    """Deliberately permissive/lenient — this endpoint's whole purpose is to
    catch errors we didn't anticipate, so we don't want a strict schema
    silently swallowing the very reports we're trying to collect. Every
    field is optional and loosely bounded in length only."""

    class Meta:
        unknown = EXCLUDE

    kind = _short_text(60)
    message = _short_text(2000)
    source = _short_text(500)
    line = fields.Int(load_default=None, allow_none=True)
    col = fields.Int(load_default=None, allow_none=True)
    stack = fields.Str(load_default="", validate=validate.Length(max=4000))
    url = _short_text(500)
    ua = _short_text(500)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------
def validate_json(schema_cls):
    """Parse + validate the request JSON body against `schema_cls`.

    On success, the validated (and type-coerced/defaulted) data is stashed
    on `flask.g.validated_data` as a plain dict for the route to use.
    On failure, short-circuits with a 400 JSON response — never a raw
    request.get_json(force=True) crash / 500.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            # Routes that serve GET (render a template) and POST (submit
            # data) from the same view only need body validation on the
            # methods that actually carry a JSON body.
            if request.method not in ("POST", "PUT", "PATCH"):
                return view_func(*args, **kwargs)

            raw = request.get_json(silent=True)
            if raw is None:
                return jsonify({"error": "invalid_json", "message": "Request body must be valid JSON"}), 400
            if not isinstance(raw, dict):
                return jsonify({"error": "invalid_json", "message": "Request body must be a JSON object"}), 400

            schema = schema_cls()
            try:
                g.validated_data = schema.load(raw)
            except ValidationError as err:
                return jsonify({"error": "validation_failed", "fields": err.messages}), 400

            return view_func(*args, **kwargs)

        return wrapper

    return decorator