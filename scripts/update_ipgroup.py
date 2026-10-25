
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import io
import ipaddress
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "tmp"
ENCODING = "gbk"
MAX_PER_GROUP = 1000

# IPv4 配置
IPV4 = {
    "input": ROOT / "ipgroup.csv",
    "output": OUTPUT_DIR / "ipgroup.csv",
    "url": (
        "https://raw.githubusercontent.com/"
        "mayaxcn/china-ip-list/refs/heads/master/chnroute.txt"
    ),
    "prefix": "CN-",
    "groups": 9,
    "version": 4,
    "required_columns": [
        "id", "comment", "type", "group_name", "addr_pool"
    ],
}

# IPv6 配置：不要求 type 字段
IPV6 = {
    "input": ROOT / "ipv6group.csv",
    "output": OUTPUT_DIR / "ipv6group.csv",
    "url": (
        "https://raw.githubusercontent.com/"
        "mayaxcn/china-ip-list/refs/heads/master/chnroute6.txt"
    ),
    "prefix": "CN6-",
    "groups": 3,
    "version": 6,
    "required_columns": [
        "id", "comment", "group_name", "addr_pool"
    ],
}


def download_networks(config):
    """下载并验证对应版本的 CIDR 网段。"""
    request = urllib.request.Request(
        config["url"],
        headers={"User-Agent": "GitHub-Actions-IPGroup-Updater"},
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read().decode("utf-8-sig")

    networks = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        try:
            network = ipaddress.ip_network(line, strict=True)
        except ValueError as exc:
            raise ValueError(
                f'{config["prefix"]} 数据源第 {line_number} 行无效：'
                f"{line}"
            ) from exc

        if network.version != config["version"]:
            raise ValueError(
                f'{config["prefix"]} 地址版本错误：{line}'
            )

        networks.append(str(network))

    if not networks:
        raise RuntimeError(
            f'{config["prefix"]} 数据源为空，停止更新。'
        )

    if len(networks) != len(set(networks)):
        raise RuntimeError(
            f'{config["prefix"]} 数据源存在重复网段，停止更新。'
        )

    capacity = config["groups"] * MAX_PER_GROUP
    if len(networks) > capacity:
        raise RuntimeError(
            f'{config["prefix"]} 共 {len(networks)} 个网段，'
            f"超过容量 {capacity}，请扩充分组。"
        )

    print(
        f'{config["prefix"]} 下载成功：{len(networks)} 个网段'
    )
    return networks


def read_csv(config):
    """读取 CSV，并按各自配置校验表头。"""
    input_file = config["input"]

    if not input_file.is_file():
        raise FileNotFoundError(f"找不到文件：{input_file}")

    with input_file.open(
        "r", encoding=ENCODING, newline=""
    ) as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise RuntimeError(f"{input_file.name} 缺少表头。")

        # 关键修正：按文件配置检查字段，不统一要求 type
        missing = (
            set(config["required_columns"])
            - set(reader.fieldnames)
        )
        if missing:
            raise RuntimeError(
                f"{input_file.name} 缺少必要字段："
                f"{sorted(missing)}"
            )

        fieldnames = reader.fieldnames
        rows = list(reader)

    names = [row["group_name"] for row in rows]

    if len(names) != len(set(names)):
        raise RuntimeError(
            f"{input_file.name} 存在重复 group_name。"
        )

    for index in range(1, config["groups"] + 1):
        name = f'{config["prefix"]}{index}'
        if name not in names:
            raise RuntimeError(
                f"{input_file.name} 缺少分组 {name}。"
            )

    return fieldnames, rows


def update_rows(config, rows, networks):
    """按原始顺序更新各分组，每组最多 1000 个网段。"""
    for index in range(config["groups"]):
        name = f'{config["prefix"]}{index + 1}'
        start = index * MAX_PER_GROUP
        group_networks = networks[start:start + MAX_PER_GROUP]

        row = next(
            item for item in rows
            if item["group_name"] == name
        )
        row["addr_pool"] = ",".join(group_networks)

        print(f"{name}: {len(group_networks)} 个网段")

    return rows


def serialize_csv(fieldnames, rows):
    """保持原有表头、列顺序及 GBK 编码。"""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode(ENCODING)


def validate_csv(config, content, expected_total):
    """校验输出文件结构、IP 版本和各分组容量。"""
    decoded = content.decode(ENCODING)
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    rows = list(reader)

    if not reader.fieldnames:
        raise RuntimeError("生成文件缺少表头。")

    missing = (
        set(config["required_columns"]) - set(reader.fieldnames)
    )
    if missing:
        raise RuntimeError(
            f"生成文件缺少字段：{sorted(missing)}"
        )

    names = [row["group_name"] for row in rows]
    if len(names) != len(set(names)):
        raise RuntimeError("生成文件存在重复 group_name。")

    expected_names = {
        f'{config["prefix"]}{i}'
        for i in range(1, config["groups"] + 1)
    }

    actual_total = 0

    for name in expected_names:
        matches = [
            row for row in rows
            if row["group_name"] == name
        ]

        if len(matches) != 1:
            raise RuntimeError(f"分组 {name} 不唯一或不存在。")

        value = matches[0]["addr_pool"]
        items = value.split(",") if value else []

        if len(items) > MAX_PER_GROUP:
            raise RuntimeError(f"{name} 超过 1000 个网段。")

        for item in items:
            try:
                network = ipaddress.ip_network(item, strict=True)
            except ValueError as exc:
                raise RuntimeError(
                    f"{name} 包含无效网段：{item}"
                ) from exc

            if network.version != config["version"]:
                raise RuntimeError(
                    f"{name} 包含错误版本的网段：{item}"
                )

        actual_total += len(items)

    if actual_total != expected_total:
        raise RuntimeError(
            f'{config["prefix"]} 总数校验失败：'
            f"预期 {expected_total}，实际 {actual_total}"
        )


def prepare_update(config):
    """完成单份 CSV 的更新及完整性检查。"""
    networks = download_networks(config)
    fieldnames, rows = read_csv(config)
    update_rows(config, rows, networks)

    content = serialize_csv(fieldnames, rows)
    validate_csv(config, content, len(networks))

    return config, content


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 两份数据均完成下载、更新和校验后才写文件
    updates = [
        prepare_update(IPV4),
        prepare_update(IPV6),
    ]

    staged_files = []

    try:
        # 先生成所有临时文件
        for config, content in updates:
            for destination in (
                config["input"],
                config["output"],
            ):
                destination.parent.mkdir(
                    parents=True, exist_ok=True
                )

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=".ipgroup-",
                    suffix=".tmp",
                    delete=False,
                ) as file:
                    file.write(content)
                    staged_files.append(
                        (Path(file.name), destination)
                    )

        # 临时文件全部成功后，再替换正式文件
        for staged, destination in staged_files:
            staged.replace(destination)
            print(f"已更新：{destination.relative_to(ROOT)}")

    finally:
        for staged, _ in staged_files:
            if staged.exists():
                staged.unlink()

    print("IPv4、IPv6 数据更新及校验全部完成。")


if __name__ == "__main__":
    main()
