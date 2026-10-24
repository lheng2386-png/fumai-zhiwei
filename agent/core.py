"""Validated snapshot tools and deterministic maintenance analysis (Python 3.12)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KINDS = {"device_specific_candidate", "shared_or_model_candidate", "insufficient_peers"}
STATUSES = {"unreviewed", "needs_investigation", "expected_operation", "data_issue", "confirmed_fault"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
RUNBOOKS = {
    "device_specific_candidate": {
        "id": "SOP-DEVICE-1", "title": "单机偏离核查", "version": "1.0",
        "checks": ["核对事件时段的限发指令、检修和设备告警记录。", "核对同群设备容量、朝向与测点单位是否可比。", "由有资质人员按现场规程检查设备及通信，补充工单与检查记录。"],
        "hypothesis": "存在单机相对同群偏低的候选证据；限发、遮挡、通信或设备异常均需核查。",
    },
    "shared_or_model_candidate": {
        "id": "SOP-SHARED-1", "title": "共性变化核查", "version": "1.0",
        "checks": ["核对同一时段的辐照度、天气与全站功率变化。", "核对并网点限发、计划检修和调度记录。", "复查参考模型适用范围与输入质量，再决定是否安排设备检查。"],
        "hypothesis": "尚无充分单机偏离证据；需要排查共性运行变化与参考模型偏差。",
    },
    "insufficient_peers": {
        "id": "SOP-DATA-1", "title": "证据完整性核查", "version": "1.0",
        "checks": ["核对通信中断、测点缺失、重复和时间错位。", "补齐同一时刻的有效其他同群观测并核对设备分组。", "数据修复后重新运行筛查，证据充分前不作设备故障归因。"],
        "hypothesis": "有效同群证据不足，应先排查数据质量。",
    },
}


class InputError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InputError("无效的设备或事件标识")
    return value


def finite(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise InputError(f"无效数值字段：{name}")
    return value


class Snapshot:
    """A server-selected, immutable snapshot. No caller-controlled paths or URLs."""

    def __init__(self, document, mode="demo", now=None):
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise InputError("需要 schema_version=1 的数据快照")
        self.mode = mode
        if mode not in {"demo", "live"} or document.get("source") != ("constructed_demo" if mode == "demo" else "measured"):
            raise InputError("数据来源与服务模式不匹配")
        for name in ("site_id", "dataset_version", "screening_version"):
            identifier(document.get(name))
        try:
            self.zone = ZoneInfo(document["timezone"])
            observed = datetime.fromisoformat(document["observed_at"])
            if observed.tzinfo is None:
                raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise InputError("时区和 observed_at 必须有效，观测时间必须带 UTC 偏移") from exc
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        if age < -300:
            raise InputError("快照观测时间在未来")
        self.observed_at = observed
        devices, events = document.get("devices"), document.get("events")
        if not isinstance(devices, list) or not 1 <= len(devices) <= 10000 or not isinstance(events, list) or len(events) > 50000:
            raise InputError("设备或事件数量超出数据合同")
        self.devices, self.events = {}, {}
        for device in devices:
            key = identifier(device.get("id"))
            identifier(device.get("peer_group"))
            if key in self.devices:
                raise InputError("重复设备标识")
            self.devices[key] = {k: device[k] for k in ("id", "peer_group")}
        for event in events:
            key = identifier(event.get("event_id"))
            if key in self.events or event.get("inverter") not in self.devices:
                raise InputError("重复事件或未知设备")
            if event.get("peer_group") != self.devices[event["inverter"]]["peer_group"]:
                raise InputError("事件同群与设备登记不一致")
            if event.get("evidence") not in KINDS or event.get("review_status") not in STATUSES:
                raise InputError("未知证据或复核状态")
            for name in ("samples", "peer_flag_samples", "min_peer_count", "observed_span_minutes"):
                finite(event.get(name), name)
                if not isinstance(event[name], int):
                    raise InputError(f"{name} 必须为整数")
            if event["samples"] < 3 or event["peer_flag_samples"] > event["samples"]:
                raise InputError("连续采样计数不符合筛查合同")
            group_count = sum(d["peer_group"] == event["peer_group"] for d in self.devices.values())
            if event["min_peer_count"] >= group_count:
                raise InputError("同群计数必须排除本机且不超过登记设备数量")
            for name in ("mean_actual_kw", "mean_reference_kw", "reference_shortfall_kwh"):
                finite(event.get(name), name)
            if event["mean_reference_kw"] <= 5:
                raise InputError("事件参考功率不满足日间筛查条件")
            if event.get("mean_peer_gap") is not None:
                gap = event["mean_peer_gap"]
                if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or not -10 <= gap <= 10:
                    raise InputError("无效同群偏离")
            if event["evidence"] == "device_specific_candidate" and (event["min_peer_count"] < 5 or event["peer_flag_samples"] < 3):
                raise InputError("单机候选缺少足够同群证据")
            try:
                start = datetime.fromisoformat(event["start_local"])
                end = datetime.fromisoformat(event["last_sample_local"])
                if start.tzinfo or end.tzinfo or (end-start).total_seconds() != (event["samples"]-1)*300:
                    raise ValueError()
                if end.replace(tzinfo=self.zone).timestamp() > observed.timestamp() + 300:
                    raise ValueError()
            except (ValueError, KeyError, TypeError) as exc:
                raise InputError("事件时间范围与连续五分钟采样不一致") from exc
            if event["observed_span_minutes"] != (event["samples"]-1)*5:
                raise InputError("采样跨度不一致")
            # Ignore arbitrary free text / upstream notes: not instructions or fault truth.
            fields = ("event_id", "inverter", "peer_group", "start_local", "last_sample_local", "samples", "observed_span_minutes", "mean_actual_kw", "mean_reference_kw", "reference_shortfall_kwh", "peer_flag_samples", "min_peer_count", "mean_peer_gap", "evidence", "review_status")
            self.events[key] = {k: event.get(k) for k in fields}
        self.meta = {k: document[k] for k in ("schema_version", "site_id", "source", "dataset_version", "screening_version", "timezone", "observed_at")}
        self.meta["fingerprint"] = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @property
    def age_seconds(self):
        return max(0, round((datetime.now(timezone.utc)-self.observed_at).total_seconds()))

    @property
    def stale(self):
        return self.age_seconds > 900

    @classmethod
    def load(cls, path, mode):
        p = Path(path)
        if p.stat().st_size > 32*1024*1024:
            raise InputError("快照超过 32 MiB")
        return cls(json.loads(p.read_text(encoding="utf-8")), mode)

    def event(self, event_id):
        identifier(event_id)
        if event_id not in self.events:
            raise InputError("未找到事件，请核对数据版本和标识")
        return dict(self.events[event_id])

    def list_events(self, device_id=None, limit=6):
        if device_id is not None and identifier(device_id) not in self.devices:
            raise InputError("未登记该设备")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 12:
            raise InputError("查询条数必须为 1–12")
        rows = [e for e in self.events.values() if device_id is None or e["inverter"] == device_id]
        rows.sort(key=lambda e: (e["review_status"] != "unreviewed", e["evidence"] != "device_specific_candidate", -e["reference_shortfall_kwh"]))
        return {"total": len(rows), "events": rows[:limit], "ordering": "待复核、单机候选、参考电量差；不是风险等级"}

    def tool(self, name, args):
        if not isinstance(args, dict):
            raise InputError("工具参数必须为对象")
        if name == "list_events" and set(args) == {"device_id", "limit"}:
            return self.list_events(**args)
        if name == "get_event" and set(args) == {"event_id"}:
            return self.event(**args)
        if name == "get_runbook" and set(args) == {"evidence"} and args["evidence"] in RUNBOOKS:
            return RUNBOOKS[args["evidence"]]
        raise InputError("工具或参数不在白名单中")

    def analyze(self, message, event_id=None, device_id=None):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
            raise InputError("请输入 1–2000 个字符的问题")
        if event_id:
            rows = [self.event(event_id)]
            if device_id and rows[0]["inverter"] != device_id:
                raise InputError("事件不属于当前设备")
        else:
            # Explicit identifiers in prose must resolve; never silently substitute another event.
            mentioned = re.findall(r"DEMO-2107-[A-Za-z0-9_-]+|\binv_[A-Za-z0-9_-]+", message)
            event_ids = [k for k in self.events if k in message]
            device_ids = [k for k in self.devices if k in message]
            for key in mentioned:
                if key not in self.events and key not in self.devices:
                    raise InputError("问题包含未登记标识，请从证据列表选择设备或事件")
            if event_ids:
                rows = [self.event(key) for key in event_ids[:6]]
            else:
                rows = self.list_events(device_id or (device_ids[0] if len(device_ids) == 1 else None))["events"]
                if len(device_ids)>1 and not device_id:
                    rows=[e for e in self.events.values() if e['inverter'] in device_ids][:6]
                selected_kind = "insufficient_peers" if "同群证据不足" in message else "shared_or_model_candidate" if "共性" in message else None
                if selected_kind:
                    rows = [e for e in self.events.values() if e["evidence"] == selected_kind and (not device_id or e["inverter"] == device_id)][:6]
        runbooks = [RUNBOOKS[k] for k in dict.fromkeys(e["evidence"] for e in rows)]
        unknowns = ["未接入现场告警、调度限发、辐照度和维修工单，无法确认故障原因。", "参考电量差不是可挽回损失，也不是设备故障概率。"]
        if self.mode == "demo":
            unknowns.insert(0, "当前是构造样例，时间为历史回放；不可据此作现场生产判断。")
        elif self.stale:
            unknowns.insert(0, "快照超过 15 分钟，仅支持历史证据回顾，不代表设备当前状态。")
        return {
            "summary": f"已检索 {len(rows)} 条候选证据。请先核对运行背景，再安排人工复核。" if rows else "当前范围没有登记事件；这不代表设备正常或不存在故障。",
            "hypotheses": [r["hypothesis"] for r in runbooks],
            "checks": list(dict.fromkeys(c for r in runbooks for c in r["checks"])),
            "unknowns": unknowns, "events": rows, "runbooks": runbooks,
            "evidence_ids": [e["event_id"] for e in rows], "metadata": self.meta,
            "stale": self.stale, "age_seconds": self.age_seconds,
            "engine": "rules", "model_status": "not_configured", "model_name": None,
            "trace": [{"tool": "get_event" if event_id else "list_events", "status": "ok", "result_count": len(rows)}, {"tool": "get_runbook", "status": "ok", "result_count": len(runbooks)}],
            "boundary": "候选证据需人工复核；本助手没有设备控制与自动派单权限。",
        }
