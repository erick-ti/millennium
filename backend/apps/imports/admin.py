from __future__ import annotations

from django.contrib import admin

from apps.imports.models import ImportBatch, ImportRow


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin[ImportBatch]):
    list_display = ["original_filename", "source_format", "status", "created_at"]
    list_filter = ["status", "source_format"]
    search_fields = ["original_filename"]
    ordering = ["-created_at", "-id"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ImportRow)
class ImportRowAdmin(admin.ModelAdmin[ImportRow]):
    list_display = ["batch", "row_number", "status", "match_confidence", "matched_printing"]
    list_select_related = ["batch", "matched_printing", "matched_printing__card"]
    list_filter = ["status", "match_confidence", "batch__source_format"]
    search_fields = ["batch__original_filename", "error_message"]
    ordering = ["batch", "row_number"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["batch", "matched_printing"]
