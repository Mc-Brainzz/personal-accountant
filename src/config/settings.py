"""
Configuration Management for Personal Accountant

Uses pydantic-settings for type-safe configuration from environment variables.

DESIGN DECISION: All configuration is centralized here.
This makes it easy to see what external dependencies exist and
ensures all required configuration is validated at startup.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudinarySettings(BaseSettings):
    """Cloudinary image enhancement service configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="CLOUDINARY_",
        extra="ignore"
    )
    
    cloud_name: str = Field(
        ...,
        description="Cloudinary cloud name"
    )
    api_key: str = Field(
        ...,
        description="Cloudinary API key"
    )
    api_secret: str = Field(
        ...,
        description="Cloudinary API secret"
    )


class MindeeSettings(BaseSettings):
    """Mindee OCR service configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="MINDEE_",
        extra="ignore"
    )
    
    api_key: str = Field(
        ...,
        description="Mindee API key"
    )


class GoogleSheetsSettings(BaseSettings):
    """Google Sheets storage configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="GOOGLE_SHEETS_",
        extra="ignore"
    )
    
    credentials_path: str = Field(
        ...,
        description="Path to Google service account credentials JSON"
    )
    spreadsheet_id: str = Field(
        ...,
        description="ID of the Google Sheets spreadsheet to use"
    )
    
    # Sheet names within the spreadsheet
    bills_sheet_name: str = Field(
        default="Bills",
        description="Name of the sheet for bills"
    )
    audit_sheet_name: str = Field(
        default="AuditLog",
        description="Name of the sheet for audit logs"
    )
    
    @field_validator('credentials_path')
    @classmethod
    def validate_credentials_path(cls, v: str) -> str:
        """Warn if credentials file doesn't exist (but don't fail - might be mounted later)."""
        if not Path(v).exists():
            import warnings
            warnings.warn(
                f"Google credentials file not found at {v}. "
                "Make sure it exists before running the application."
            )
        return v


class GeminiSettings(BaseSettings):
    """Gemini LLM configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        extra="ignore"
    )
    
    api_key: str = Field(
        ...,
        description="Gemini API key"
    )
    model_name: str = Field(
        default="gemini-1.5-flash",
        description="Gemini model to use"
    )
    # Safety settings
    max_tokens: int = Field(
        default=2048,
        ge=100,
        le=8192,
        description="Maximum tokens in response"
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Model temperature (lower = more deterministic)"
    )


class AppSettings(BaseSettings):
    """
    Main application settings.
    
    Loads configuration from environment variables and .env file.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Environment
    app_environment: str = Field(
        default="development",
        description="Application environment"
    )
    debug_mode: bool = Field(
        default=False,
        description="Enable debug mode"
    )
    
    # File upload limits
    max_upload_size_mb: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum upload file size in MB"
    )
    supported_image_formats: str = Field(
        default="jpg,jpeg,png,webp",
        description="Comma-separated list of supported image formats"
    )
    
    # Quality thresholds
    min_image_quality_score: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum image quality score to proceed"
    )
    min_ocr_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum OCR confidence to proceed"
    )
    
    # Validation thresholds
    max_bill_amount_inr: float = Field(
        default=1000000.0,
        description="Maximum reasonable bill amount (for sanity checking)"
    )
    future_date_tolerance_days: int = Field(
        default=7,
        description="How many days in the future a bill date can be"
    )
    
    @property
    def supported_formats_list(self) -> list[str]:
        """Get supported formats as a list."""
        return [fmt.strip().lower() for fmt in self.supported_image_formats.split(",")]
    
    @property
    def max_upload_size_bytes(self) -> int:
        """Get max upload size in bytes."""
        return self.max_upload_size_mb * 1024 * 1024


class Settings(BaseSettings):
    """
    Root settings container.
    
    Aggregates all sub-settings for easy access.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Load all sub-settings
    # Note: These are loaded lazily to allow partial configuration
    
    @property
    def cloudinary(self) -> CloudinarySettings:
        return CloudinarySettings()
    
    @property
    def mindee(self) -> MindeeSettings:
        return MindeeSettings()
    
    @property
    def google_sheets(self) -> GoogleSheetsSettings:
        return GoogleSheetsSettings()
    
    @property
    def gemini(self) -> GeminiSettings:
        return GeminiSettings()
    
    @property
    def app(self) -> AppSettings:
        return AppSettings()


@lru_cache()
def get_settings() -> Settings:
    """
    Get application settings (cached).
    
    Uses LRU cache to ensure settings are only loaded once.
    Call get_settings.cache_clear() to reload if needed.
    """
    return Settings()


def validate_all_settings() -> dict[str, bool]:
    """
    Validate all settings are properly configured.
    
    Returns a dict of {setting_name: is_valid}.
    Useful for startup checks.
    """
    results = {}
    
    settings = get_settings()
    
    # Check each service
    try:
        _ = settings.cloudinary
        results["cloudinary"] = True
    except Exception as e:
        results["cloudinary"] = False
        results["cloudinary_error"] = str(e)
    
    try:
        _ = settings.mindee
        results["mindee"] = True
    except Exception as e:
        results["mindee"] = False
        results["mindee_error"] = str(e)
    
    try:
        _ = settings.google_sheets
        results["google_sheets"] = True
    except Exception as e:
        results["google_sheets"] = False
        results["google_sheets_error"] = str(e)
    
    try:
        _ = settings.gemini
        results["gemini"] = True
    except Exception as e:
        results["gemini"] = False
        results["gemini_error"] = str(e)
    
    try:
        _ = settings.app
        results["app"] = True
    except Exception as e:
        results["app"] = False
        results["app_error"] = str(e)
    
    return results
