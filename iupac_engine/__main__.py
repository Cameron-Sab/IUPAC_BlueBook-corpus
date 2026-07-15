from __future__ import annotations

import argparse
import json
import sys

from .engine import name_smiles
from .rule_engine import BlueBookRuleEngine


def main() -> int:
    legacy_commands = {"name", "stats", "rule", "search", "deps", "eval", "-h", "--help"}
    if len(sys.argv) > 1 and sys.argv[1] not in legacy_commands:
        sys.argv.insert(1, "name")

    parser = argparse.ArgumentParser(description="Blue Book semantic rule engine and scoped SMILES prototype")
    subparsers = parser.add_subparsers(dest="command")

    name_parser = subparsers.add_parser("name", help="run the old scoped SMILES naming prototype")
    name_parser.add_argument("smiles", help="SMILES string to name")
    name_parser.add_argument("--explain", action="store_true", help="include decision trace")
    name_parser.add_argument("--json", action="store_true", help="emit full JSON response")

    subparsers.add_parser("stats", help="show rule corpus and dependency graph stats")

    rule_parser = subparsers.add_parser("rule", help="show one semantic rule record")
    rule_parser.add_argument("rule_id")
    rule_parser.add_argument("--json", action="store_true")

    search_parser = subparsers.add_parser("search", help="search semantic rules")
    search_parser.add_argument("query", nargs="?", default="")
    search_parser.add_argument("--chapter")
    search_parser.add_argument("--type", dest="rule_type")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    deps_parser = subparsers.add_parser("deps", help="walk rule dependency graph")
    deps_parser.add_argument("rule_id")
    deps_parser.add_argument("--depth", type=int, default=1)
    deps_parser.add_argument("--reverse", action="store_true", help="show incoming dependents")
    deps_parser.add_argument("--json", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="activate rules from simple fact tokens")
    eval_parser.add_argument("--fact", action="append", default=[], help="fact/predicate token; repeatable")
    eval_parser.add_argument("--chapter")
    eval_parser.add_argument("--type", dest="rule_type")
    eval_parser.add_argument("--limit", type=int, default=20)
    eval_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "name":
        return _name(args)

    rule_engine = BlueBookRuleEngine()
    if args.command == "stats":
        print(json.dumps(rule_engine.stats(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "rule":
        return _rule(rule_engine, args)
    if args.command == "search":
        return _search(rule_engine, args)
    if args.command == "deps":
        return _deps(rule_engine, args)
    if args.command == "eval":
        return _eval(rule_engine, args)
    return 0


def _name(args: argparse.Namespace) -> int:

    result = name_smiles(args.smiles, explain=args.explain)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["name"] if result["status"] == "success" else f"{result['status']}: {result['reason']}")
        if args.explain:
            for step in result.get("decision_trace", []):
                print(f"- {step['rule_id']}: {step['message']}")
    return 0


def _rule(rule_engine: BlueBookRuleEngine, args: argparse.Namespace) -> int:
    rules = rule_engine.get(args.rule_id)
    if args.json:
        print(json.dumps([rule.as_dict() for rule in rules], indent=2, ensure_ascii=False))
    elif not rules:
        print(f"No rule found for {args.rule_id}")
    else:
        for rule in rules:
            print(f"{rule.rule_id} [{rule.source_chapter}] {rule.title}")
            print(f"type: {rule.rule_type}")
            if rule.depends_on:
                print("depends_on: " + ", ".join(rule.depends_on))
            if rule.applies_if:
                print("if:")
                for item in rule.applies_if[:8]:
                    print(f"  - {item}")
            if rule.unless:
                print("unless:")
                for item in rule.unless[:8]:
                    print(f"  - {item}")
            if rule.then:
                print("then:")
                for item in rule.then[:8]:
                    print(f"  - {item}")
            if rule.prefer:
                print("prefer:")
                for item in rule.prefer[:8]:
                    print(f"  - {item}")
    return 0


def _search(rule_engine: BlueBookRuleEngine, args: argparse.Namespace) -> int:
    matches = rule_engine.search(args.query, chapter=args.chapter, rule_type=args.rule_type, limit=args.limit)
    if args.json:
        print(json.dumps([rule.as_dict() for rule in matches], indent=2, ensure_ascii=False))
    else:
        for rule in matches:
            print(f"{rule.rule_id}\t{rule.source_chapter}\t{rule.rule_type}\t{rule.title}")
    return 0


def _deps(rule_engine: BlueBookRuleEngine, args: argparse.Namespace) -> int:
    result = rule_engine.dependencies(args.rule_id, depth=args.depth, reverse=args.reverse)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result['root']} {result['direction']} dependencies, depth {result['depth']}")
        for edge in result["edges"][:100]:
            marker = "" if edge.get("target_in_corpus") else " (external/prefix target)"
            print(f"{edge['source']} -> {edge['target']}{marker}")
    return 0


def _eval(rule_engine: BlueBookRuleEngine, args: argparse.Namespace) -> int:
    result = rule_engine.evaluate(args.fact, chapter=args.chapter, rule_type=args.rule_type, limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"activated: {result['activated_count']} blocked: {result['blocked_count']}")
        for item in result["activated"]:
            print(f"{item['rule_id']}\t{item['source_chapter']}\t{item['rule_type']}\t{item['title']}")
            for action in item["actions"][:3]:
                print(f"  then: {action}")
            for pref in item["preferences"][:3]:
                print(f"  prefer: {pref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
