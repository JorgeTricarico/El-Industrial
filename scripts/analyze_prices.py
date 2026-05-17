#!/usr/bin/env python3
"""Analisis estadistico de cambio de precios entre dos snapshots de un tenant.

Uso:
    analyze_prices.py                          # primer tenant active, ultimo vs anteultimo .gz
    analyze_prices.py --tenant <slug>          # tenant especifico
    analyze_prices.py --tenant <slug> --old <path> --new <path>  # archivos especificos

Multi-tenant aware desde 2026-05-17 (M1).
"""
import argparse
import csv
import gzip
import json
import os
import sys
from collections import Counter
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

sys.path.insert(0, SCRIPT_DIR)
import update_products as up  # noqa: E402


def load_gz_json(filename):
    if not filename or not os.path.exists(filename):
        print(f"File {filename} not found.")
        return []
    with gzip.open(filename, "rt", encoding="utf-8") as f:
        return json.load(f)


def find_snapshots(tenant_dir):
    """Devuelve (anteultimo, ultimo) gz absolutos del tenant. None si no hay 2."""
    data_dir = os.path.join(tenant_dir, "data")
    if not os.path.isdir(data_dir):
        return None, None
    gz = sorted([f for f in os.listdir(data_dir) if f.endswith(".gz")])
    if len(gz) < 2:
        return None, None
    return (
        os.path.join(data_dir, gz[-2]),
        os.path.join(data_dir, gz[-1]),
    )


def pick_tenant(slug=None):
    tenants = up.load_registry()
    actives = [t for t in tenants if t.get("state") == "active"]
    if slug:
        for t in tenants:
            if t.get("slug") == slug:
                return t
        raise SystemExit(f"tenant '{slug}' no esta en _registry.yml")
    if not actives:
        raise SystemExit("no hay tenants active en _registry.yml")
    return actives[0]


def run_analysis(slug, old_file, new_file):
    print(f"Analyzing changes between {os.path.basename(old_file)} and {os.path.basename(new_file)} (tenant {slug})...")

    old_list = load_gz_json(old_file)
    new_list = load_gz_json(new_file)

    if not old_list or not new_list:
        print("Data missing for analysis.")
        return False

    old_data = {p["producto"]: p for p in old_list}
    new_data = {p["producto"]: p for p in new_list}

    matches = []
    for code, new_item in new_data.items():
        if code in old_data:
            old_item = old_data[code]
            try:
                old_p = float(old_item["precio"])
                new_p = float(new_item["precio"])
                if old_p > 0:
                    percent_change = (new_p - old_p) / old_p * 100
                    matches.append({
                        "code": code,
                        "desc": new_item["detalle"],
                        "brand": new_item.get("marca", "Sin Marca"),
                        "old": old_p,
                        "new": new_p,
                        "change": percent_change
                    })
            except (TypeError, ValueError, KeyError):
                continue

    if not matches:
        print("No products matched for analysis.")
        return False

    total_matched = len(matches)
    all_changes = [m["change"] for m in matches]
    avg_increase = sum(all_changes) / total_matched
    rounded_changes = [round(c, 1) for c in all_changes]
    top_rates = Counter(rounded_changes).most_common(5)

    brand_stats = {}
    for m in matches:
        brand_stats.setdefault(m["brand"], []).append(m["change"])

    brand_summary = sorted(
        [{"brand": b, "avg_increase": sum(c)/len(c), "count": len(c)} for b, c in brand_stats.items()],
        key=lambda x: x["avg_increase"], reverse=True,
    )

    timestamp = datetime.now().strftime("%Y-%m-%d")
    tenant_reports = os.path.join(REPORTS_DIR, slug)
    os.makedirs(tenant_reports, exist_ok=True)
    md_file = os.path.join(tenant_reports, f"analisis_precios_{timestamp}.md")
    csv_file = os.path.join(tenant_reports, f"analisis_precios_detallado_{timestamp}.csv")

    print(f"Generating reports in {tenant_reports}...")
    with open(md_file, "w") as f:
        f.write(f"# Analisis estadistico de cambio de precios — {slug}\n")
        f.write(f"**Periodo:** {os.path.basename(old_file)} ➔ {os.path.basename(new_file)}\n\n")
        f.write(f"## Resumen ejecutivo\n- **Productos analizados:** {total_matched}\n- **Aumento promedio global:** {avg_increase:.2f}%\n- **Tasa mas comun:** {top_rates[0][0]:.1f}% ({top_rates[0][1]} productos)\n\n")
        f.write("## Aumento promedio por marca (Top 20)\n| Marca | Aumento | Cantidad |\n| --- | --- | --- |\n")
        for b in brand_summary[:20]:
            f.write(f"| {b['brand']} | {b['avg_increase']:.2f}% | {b['count']} |\n")

    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Codigo", "Descripcion", "Marca", "Precio Viejo", "Precio Nuevo", "Cambio Pesos", "Cambio Porcentaje"])
        for m in sorted(matches, key=lambda x: x["change"], reverse=True):
            writer.writerow([m["code"], m["desc"], m["brand"], f"{m['old']:.2f}", f"{m['new']:.2f}", f"{(m['new']-m['old']):.2f}", f"{m['change']:.2f}%"])

    print(f"Success. Analysis reports saved to {tenant_reports}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", help="slug del tenant (default: primer active)")
    parser.add_argument("--old", help="path absoluto al gz viejo (default: anteultimo del tenant)")
    parser.add_argument("--new", help="path absoluto al gz nuevo (default: ultimo del tenant)")
    args = parser.parse_args(argv)

    tenant = pick_tenant(args.tenant)
    slug = tenant["slug"]

    if args.old and args.new:
        old_file, new_file = args.old, args.new
    else:
        old_file, new_file = find_snapshots(os.path.join(TENANTS_DIR, slug))
        if not old_file or not new_file:
            print(f"❌ tenant {slug}: hacen falta al menos 2 .gz en data/", file=sys.stderr)
            return False

    return run_analysis(slug, old_file, new_file)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
