
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import io
import ipaddress
import shutil
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "tmp"
ENCODING = "gbk"

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
}

# IPv6 配置
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
}

MAX_PER_GROUP = 1000

REQUIRED_COLUMNS = {
    "id", "comment", "type", "group_name", "addr_pool"
}


def download_networks(config):
    """下载并验证 IPv4 或 IPv6 CIDR 列表。"""
    request = urllib.request.Request(
        config["url"],
        headers={
            "User-Agent": "GitHub-Actions-IPGroup-Updater"
        },
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
                f'{config["prefix"]} 数据源第 {line_number} 行格式错误：'
                f"{line}"
            ) from exc

        if network.version != config["version"]:
            raise ValueError(
                f'{config["prefix"]} 数据源地址类型错误：{line}'
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
            f"超过最大容量 {capacity}，请扩充分组。"
        )

    print(
        f'{config["prefix"]} 下载完成：'
        f"{len(networks)} 个 IPv{config['version']} 网段"
    )

    return networks


def read_csv(config):
    """读取原 CSV，检查表头及分组是否存在。"""
    input_file = config["input"]

    if not input_file.is_file():
        raise FileNotFoundError(f"找不到文件：{input_file}")

    with input_file.open(
        "r", encoding=ENCODING, newline=""
    ) as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise RuntimeError(f"{input_file.name} 缺少表头。")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)

        if missing:
            raise RuntimeError(
                f"{input_file.name} 缺少字段：{sorted(missing)}"
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
    """按源数据顺序分组，每组最多 1000 个网段。"""
    for index in range(config["groups"]):
        name = f'{config["prefix"]}{index + 1}'

        start = index * MAX_PER_GROUP
        end = start + MAX_PER_GROUP
        group_networks = networks[start:end]

        row = next(
            item for item in rows
            if item["group_name"] == name
        )

        row["addr_pool"] = ",".join(group_networks)

        print(f"{name}: {len(group_networks)} 个网段")

    return rows


def serialize_csv(fieldnames, rows):
    """生成保持原字段顺序及 GBK 编码的 CSV 字节。"""
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
    """回读验证生成文件，确保分组容量和总数正确。"""
    decoded = content.decode(ENCODING)
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    rows = list(reader)

    if not reader.fieldnames:
        raise RuntimeError("生成的 CSV 缺少表头。")

    names = [row["group_name"] for row in rows]

    for index in range(1, config["groups"] + 1):
        name = f'{config["prefix"]}{index}'

        matches = [
            row for row in rows
            if row["group_name"] == name
        ]

        if len(matches) != 1:
            raise RuntimeError(f"生成文件中的 {name} 不唯一。")

        value = matches[0]["addr_pool"]
        items = value.split(",") if value else []

        if len(items) > MAX_PER_GROUP:
            raise RuntimeError(f"{name} 超过 1000 个网段。")

        for item in items:
            network = ipaddress.ip_network(item, strict=True)

            if network.version != config["version"]:
                raise RuntimeError(
                    f"{name} 包含错误类型的网段：{item}"
                )

    actual_total = sum(
        len(row["addr_pool"].split(","))
        for row in rows
        if row["group_name"].startswith(config["prefix"])
        and row["addr_pool"]
    )

    if actual_total != expected_total:
        raise RuntimeError(
            f"{config['prefix']} 总数校验失败："
            f"预期 {expected_total}，实际 {actual_total}"
        )


def prepare_update(config):
    """完成单份 CSV 的读取、更新、序列化和校验。"""
    networks = download_networks(config)
    fieldnames, rows = read_csv(config)
    update_rows(config, rows, networks)

    content = serialize_csv(fieldnames, rows)
    validate_csv(config, content, len(networks))

    return config, content


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 两份数据全部处理和校验通过后，才开始写入文件
    updates = [
        prepare_update(IPV4),
        prepare_update(IPV6),
    ]

    # 先在各自目标目录中写入临时文件
    staged_files = []

    try:
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

        # 全部临时文件生成后再替换目标文件
        for staged, destination in staged_files:
            staged.replace(destination)
            print(f"已更新：{destination.relative_to(ROOT)}")

    finally:
        for staged, _ in staged_files:
            if staged.exists():
                staged.unlink()

    print("IPv4 和 IPv6 分组数据更新及校验完成。")


if __name__ == "__main__":
    main()
