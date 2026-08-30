# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The SPA exposes pull delivery only for devices that can collect books."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_device_list_marks_only_pull_readers_as_delivery_targets():
    annotations = _source("cps/annotations.py")

    assert '"can_receive_books": device.kind in ("kobo", "koreader")' in annotations


def test_book_detail_queues_to_a_selected_active_device():
    queries = _source("frontend/src/lib/queries.ts")
    detail = _source("frontend/src/pages/BookDetail.tsx")

    assert "useActiveDeliveryDevices" in queries
    assert "/api/annotations/devices?active=true" in queries
    assert "useQueueDeviceDelivery" in queries
    assert "device-deliveries" in queries
    assert "can_receive_books" in queries
    assert "Send to device" in detail
    assert "select" in detail
    assert "aria-describedby" in detail
    assert 'role="status"' in detail


def test_send_to_device_spa_strings_are_extraction_anchors():
    strings = _source("cps/spa_strings.py")

    for message in (
        "Send to device",
        "Device",
        "Collects on the device's next sync.",
        "Choose a device",
        "Queueing…",
        "Book queued for this device",
        "Could not queue this book for the device.",
    ):
        assert f'_("{message}")' in strings


def test_device_inventory_exposes_named_delete_and_storage_status():
    devices = _source("frontend/src/pages/Devices.tsx")
    inventory = _source("frontend/src/components/DeviceInventory.tsx")
    strings = _source("cps/spa_strings.py")

    assert "inventory_item_id" in inventory
    assert "Delete from device" in inventory
    assert "storage_free" in devices
    assert "storage_total" in devices
    assert "/inventory/${book.inventory_item_id}/delete" in inventory
    for message in (
        "Delete from device",
        "Deletion requested",
        "Could not request deletion from this device.",
        "{free} free of {total}",
    ):
        assert f'_("{message}")' in strings
