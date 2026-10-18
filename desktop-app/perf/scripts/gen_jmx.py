#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置驱动的 JMX 生成器（**零业务硬编码**）。

把「场景定义」从手写的、把端点 / 夹具路径 / 端口写死在 .py 里的硬编码生成脚本，
迁移到 `config/jmeter-config.yml` 的 `scenarios` 段。本脚本只实现通用生成算法：

  - 命名线程档（thread_profiles）：{threads, ramp, duration}
  - 命名夹具（fixtures）：逗号分隔的多个真实值，用 {{name.0}} / {{name.1}} 引用
  - 每个 scenario = 一份 JMX：含一个或多个采样器（端点 + 方法 + 参数 / 原始 JSON 体）
  - 可选 loop：把场景展开成多份 JMX（如 scale_T1..T16）
  - {n} / {{fixture...}} 占位符在生成期替换

生成的 JMX 严格遵循回放引擎解析契约（见 references/lessons_learned.md §2 #4/#5）：
  - 递归 emitter：每个组件后跟兄弟 hashTree，避免静默吞采样器
  - ThreadGroup 用 scheduler + duration；loops=-1
  - GET 具名参数 postBodyRaw=false（引擎 finalize_url 会拼 query）；
    POST 原始 JSON 用 postBodyRaw=true + 单 HTTPArgument(name="", value=json)
  - 主机/端口来自 ${__P(host,localhost)} / ${__P(port,8080)}，运行期 -Jhost/-Jport 覆盖

仅依赖标准库 + PyYAML（与引擎一致）。用法：
  python scripts/gen_jmx.py --config config/jmeter-config.yml
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import xml.sax.saxutils as X


# ===========================================================================
# 模板替换（生成期）
# ===========================================================================
def expand_template(value, fixtures: dict, loop_val):
    """替换 {n}（loop 变量）与 {{fixture}} / {{fixture.i}}（命名夹具）。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: expand_template(v, fixtures, loop_val) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    s = value
    if loop_val is not None:
        s = s.replace("{n}", str(loop_val))

    def repl(m):
        key = m.group(1).strip()
        name, idx = (key.split(".", 1) + [None])[:2] if "." in key else (key, None)
        raw = fixtures.get(name, "")
        parts = [p.strip() for p in str(raw).split(",")]
        if idx is not None:
            try:
                i = int(idx)
            except ValueError:
                i = 0
            return parts[i] if 0 <= i < len(parts) else (parts[0] if parts else "")
        return parts[0] if parts else ""

    return re.sub(r"\{\{([^}]+)\}\}", repl, s)


# ===========================================================================
# 组件 emitter（严格 hashTree 配对，规避 JMeter 加载期校验陷阱）
# ===========================================================================
def _node(tag_open_close: str, inner: str = "") -> str:
    """包裹 hashTree：有 inner 则 <hashTree>inner</hashTree>，否则自闭合 <hashTree/>。"""
    if inner:
        return tag_open_close + "<hashTree>" + inner + "</hashTree>"
    return tag_open_close + "<hashTree/>"


def _sampler(name: str, method: str, path: str, params: dict, raw_body, protocol: str) -> str:
    """单个 HTTPSamplerProxy。params 非空 → 表单（GET 走 query）；raw_body 非空 → JSON 原始体。"""
    if raw_body:
        args = (
            '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" '
            'guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
            '<collectionProp name="Arguments.arguments">'
            '<elementProp name="" elementType="HTTPArgument">'
            '<boolProp name="HTTPArgument.always_encode">false</boolProp>'
            '<stringProp name="Argument.name"></stringProp>'
            '<stringProp name="Argument.value">%s</stringProp>'
            '<stringProp name="Argument.metadata">=</stringProp>'
            '</elementProp>'
            '</collectionProp></elementProp>' % X.escape(raw_body)
        )
        post_raw = "true"
    else:
        parts = []
        for k, v in (params or {}).items():
            parts.append(
                '<elementProp name="%s" elementType="HTTPArgument">'
                '<boolProp name="HTTPArgument.always_encode">true</boolProp>'
                '<stringProp name="Argument.name">%s</stringProp>'
                '<stringProp name="Argument.value">%s</stringProp>'
                '<stringProp name="Argument.metadata">=</stringProp>'
                '</elementProp>' % (X.escape(str(k)), X.escape(str(k)), X.escape(str(v)))
            )
        args = (
            '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" '
            'guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
            '<collectionProp name="Arguments.arguments">%s</collectionProp>'
            '</elementProp>' % "".join(parts)
        )
        post_raw = "false"
    return (
        '<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" '
        'testname="%s" enabled="true">%s'
        '<stringProp name="HTTPSampler.domain">${__P(host,localhost)}</stringProp>'
        '<stringProp name="HTTPSampler.port">${__P(port,8080)}</stringProp>'
        '<stringProp name="HTTPSampler.protocol">%s</stringProp>'
        '<stringProp name="HTTPSampler.path">%s</stringProp>'
        '<stringProp name="HTTPSampler.method">%s</stringProp>'
        '<boolProp name="HTTPSampler.follow_redirects">true</boolProp>'
        '<boolProp name="HTTPSampler.use_keepalive">true</boolProp>'
        '<boolProp name="HTTPSampler.postBodyRaw">%s</boolProp>'
        '</HTTPSamplerProxy>'
        % (X.escape(name), args, X.escape(protocol), X.escape(path), method.upper(), post_raw)
    )


def _thread_group(tg_name: str, profile: dict, samplers_xml: str) -> str:
    tg = (
        '<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="%s" enabled="true">'
        '<stringProp name="ThreadGroup.on_sample_error">continue</stringProp>'
        '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" '
        'guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">'
        '<boolProp name="LoopController.continue_forever">false</boolProp>'
        '<stringProp name="LoopController.loops">-1</stringProp>'
        '</elementProp>'
        '<stringProp name="ThreadGroup.num_threads">%d</stringProp>'
        '<stringProp name="ThreadGroup.ramp_time">%d</stringProp>'
        '<boolProp name="ThreadGroup.scheduler">true</boolProp>'
        '<stringProp name="ThreadGroup.duration">%d</stringProp>'
        '<stringProp name="ThreadGroup.delay"></stringProp>'
        '<boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>'
        '</ThreadGroup>' % (X.escape(tg_name), int(profile["threads"]), int(profile["ramp"]),
                            int(profile["duration"]))
    )
    rc = (
        '<ResultCollector guiclass="SummaryReport" testclass="ResultCollector" '
        'testname="Summary Report" enabled="true">'
        '<boolProp name="ResultCollector.error_logging">false</boolProp>'
        '<stringProp name="filename"></stringProp>'
        '</ResultCollector>'
    )
    # ThreadGroup 的 hashTree 内含各采样器（各自 <hashTree/>）+ ResultCollector（<hashTree/>）
    inner = samplers_xml + _node(rc)
    return _node(tg, inner)


def _testplan(testname: str) -> str:
    tp = (
        '<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="%s" enabled="true">'
        '<stringProp name="TestPlan.comments"></stringProp>'
        '<boolProp name="TestPlan.functional_mode">false</boolProp>'
        '<boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>'
        '<boolProp name="TestPlan.serialize_threadgroups">false</boolProp>'
        '<elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" '
        'testclass="Arguments" testname="User Defined Variables" enabled="true">'
        '<collectionProp name="Arguments.arguments"/></elementProp>'
        '<stringProp name="TestPlan.user_define_classpath"></stringProp>'
        '</TestPlan>' % X.escape(testname)
    )
    return tp


def build_jmx(testname: str, tg_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">\n'
        '<hashTree>\n'
        + _node(_testplan(testname), tg_xml)
        + '\n</hashTree>\n</jmeterTestPlan>\n'
    )


# ===========================================================================
# 场景展开
# ===========================================================================
def render_one(scn: dict, profile: dict, fixtures: dict, protocol: str, loop_val) -> "tuple[str, str]":
    name = expand_template(scn.get("name", "plan"), fixtures, loop_val)
    samplers_xml = ""
    for sm in scn.get("samplers", []):
        method = (sm.get("method") or "GET").upper()
        path = expand_template(sm.get("path", "/"), fixtures, loop_val)
        params = expand_template(sm.get("params"), fixtures, loop_val) or {}
        raw_body = expand_template(sm.get("body"), fixtures, loop_val)
        if raw_body and not isinstance(raw_body, str):
            raw_body = str(raw_body)
        samplers_xml += _node(_sampler(sm.get("name", path), method, path, params, raw_body, protocol))
    tg = _thread_group(name, profile, samplers_xml)
    return name, build_jmx(name, tg)


def generate(cfg: dict) -> "list[str]":
    sc = cfg.get("scenarios", {}) or {}
    if not sc.get("enabled", False):
        print("[gen_jmx] scenarios.enabled=false，跳过生成（如需生成请置 true）")
        return []
    base = os.path.dirname(os.path.abspath(cfg["__config_path__"]))
    out_dir = os.path.join(base, sc.get("output_jmx_dir", "jmeter"))
    os.makedirs(out_dir, exist_ok=True)
    protocol = sc.get("default_protocol", "http")
    fixtures = sc.get("fixtures", {}) or {}
    profiles = sc.get("thread_profiles", {}) or {}

    written = []
    for scn in sc.get("list", []) or []:
        profile_name = scn.get("profile", "smoke")
        profile = profiles.get(profile_name, {"threads": 1, "ramp": 1, "duration": 30})
        loops = scn.get("loop") or [None]
        for n in loops:
            name, xml = render_one(scn, profile, fixtures, protocol, n)
            path = os.path.join(out_dir, name + ".jmx")
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml)
            written.append(path)
            print("wrote %s (profile=%s, samplers=%d)" % (path, profile_name, len(scn.get("samplers", []))))
    return written


def main():
    ap = argparse.ArgumentParser(description="配置驱动 JMX 生成器（零硬编码）")
    ap.add_argument("--config", required=True, help="jmeter-config.yml 路径")
    a = ap.parse_args()
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 PyYAML：请先 `pip install pyyaml`")
    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["__config_path__"] = os.path.abspath(a.config)
    out = generate(cfg)
    if out:
        print("ALL JMX DONE (%d files)" % len(out))


if __name__ == "__main__":
    main()
