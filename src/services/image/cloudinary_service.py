"""
Image Enhancement Service using Cloudinary

DESIGN DECISION: We use Cloudinary because:
1. Excellent image enhancement capabilities (sharpening, contrast, auto-quality)
2. Reliable cloud infrastructure
3. Simple API
4. Free tier sufficient for personal use

This service handles:
1. Image upload to Cloudinary
2. Enhancement transformations
3. Quality assessment
4. Returning enhanced image URL

CRITICAL: We do NOT trust OCR on poor quality images.
If enhancement fails or quality is too low, we STOP and ask user to retake.
"""

import hashlib
from datetime import datetime
from io import BytesIO
from typing import Optional
from uuid import UUID

import cloudinary
import cloudinary.uploader
from cloudinary import CloudinaryImage
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.models.bill import EnhancedImage, ImageQuality, ImageUpload


class ImageEnhancementError(Exception):
    """Base exception for image enhancement errors."""
    pass


class ImageTooBlurryError(ImageEnhancementError):
    """Image is too blurry even after enhancement."""
    pass


class ImageUploadError(ImageEnhancementError):
    """Failed to upload image to Cloudinary."""
    pass


class CloudinaryImageService:
    """
    Service for image enhancement using Cloudinary.
    
    Flow:
    1. Receive raw image bytes
    2. Upload to Cloudinary with enhancement transformations
    3. Assess quality of enhanced image
    4. Return enhanced image URL or raise error if unusable
    """
    
    def __init__(self):
        self._settings = get_settings().cloudinary
        self._app_settings = get_settings().app
        self._configured = False
    
    def _configure(self):
        """Configure Cloudinary SDK."""
        if not self._configured:
            cloudinary.config(
                cloud_name=self._settings.cloud_name,
                api_key=self._settings.api_key,
                api_secret=self._settings.api_secret,
                secure=True,
            )
            self._configured = True
    
    def _generate_public_id(self, upload_id: UUID, filename: str) -> str:
        """
        Generate a unique public ID for Cloudinary.
        
        Format: bills/{upload_id}_{filename_hash}
        """
        filename_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
        return f"bills/{upload_id}_{filename_hash}"
    
    def _assess_image_quality(
        self,
        image_bytes: bytes,
    ) -> tuple[ImageQuality, float, list[str]]:
        """
        Assess image quality using PIL.
        
        Returns: (quality_enum, quality_score, list_of_issues)
        
        DESIGN DECISION: We use simple heuristics rather than ML-based quality
        assessment because:
        1. Lower latency
        2. More predictable behavior
        3. No additional API costs
        4. Good enough for our use case
        """
        issues = []
        score = 1.0
        
        try:
            img = Image.open(BytesIO(image_bytes))
            width, height = img.size
            
            # Check resolution
            min_dimension = min(width, height)
            if min_dimension < 300:
                issues.append("Image resolution too low (minimum 300px on smallest side)")
                score -= 0.4
            elif min_dimension < 500:
                issues.append("Image resolution is low, text may be hard to read")
                score -= 0.2
            
            # Check aspect ratio (very extreme ratios are suspicious)
            aspect = max(width, height) / min(width, height)
            if aspect > 5:
                issues.append("Unusual aspect ratio - image may be cropped incorrectly")
                score -= 0.2
            
            # Check if image is too dark or too bright (using histogram)
            if img.mode != "L":
                gray = img.convert("L")
            else:
                gray = img
            
            histogram = gray.histogram()
            total_pixels = sum(histogram)
            
            # Check for very dark images (most pixels in low values)
            dark_pixels = sum(histogram[:50]) / total_pixels
            if dark_pixels > 0.7:
                issues.append("Image is very dark - please take photo in better lighting")
                score -= 0.3
            
            # Check for very bright/washed out images
            bright_pixels = sum(histogram[200:]) / total_pixels
            if bright_pixels > 0.7:
                issues.append("Image is overexposed - please reduce lighting or angle")
                score -= 0.3
            
            # Check for very low contrast
            # Find the range of pixel values that contain 90% of pixels
            cumsum = 0
            low_percentile = 0
            high_percentile = 255
            
            for i, count in enumerate(histogram):
                cumsum += count
                if cumsum >= total_pixels * 0.05 and low_percentile == 0:
                    low_percentile = i
                if cumsum >= total_pixels * 0.95:
                    high_percentile = i
                    break
            
            contrast_range = high_percentile - low_percentile
            if contrast_range < 50:
                issues.append("Image has very low contrast - text may be hard to read")
                score -= 0.25
            
        except Exception as e:
            issues.append(f"Could not analyze image: {str(e)}")
            score = 0.3  # Unknown quality, be conservative
        
        # Clamp score
        score = max(0.0, min(1.0, score))
        
        # Determine quality category
        if score >= 0.7:
            quality = ImageQuality.GOOD
        elif score >= 0.5:
            quality = ImageQuality.ACCEPTABLE
        elif score >= 0.3:
            quality = ImageQuality.POOR
        else:
            quality = ImageQuality.UNUSABLE
        
        return quality, score, issues
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def enhance_image(
        self,
        image_bytes: bytes,
        upload: ImageUpload,
    ) -> EnhancedImage:
        """
        Upload and enhance an image using Cloudinary.
        
        ENHANCEMENT TRANSFORMATIONS APPLIED:
        1. Auto contrast and brightness (improve)
        2. Sharpen (for text clarity)
        3. Auto format (best quality for size)
        
        Args:
            image_bytes: Raw image bytes
            upload: ImageUpload metadata
            
        Returns:
            EnhancedImage with URL and quality assessment
            
        Raises:
            ImageTooBlurryError: If image quality is too poor
            ImageUploadError: If upload fails
        """
        self._configure()
        
        # First, assess the original image quality
        pre_quality, pre_score, pre_issues = self._assess_image_quality(image_bytes)
        
        # If original is completely unusable, don't even try
        if pre_quality == ImageQuality.UNUSABLE:
            return EnhancedImage(
                upload_id=upload.upload_id,
                enhanced_at=datetime.utcnow(),
                cloudinary_url="",
                quality_assessment=ImageQuality.UNUSABLE,
                quality_score=pre_score,
                enhancement_applied=[],
                quality_issues=pre_issues,
            )
        
        # Upload to Cloudinary with enhancements
        try:
            public_id = self._generate_public_id(
                upload.upload_id,
                upload.original_filename,
            )
            
            # Upload with eager transformations
            result = cloudinary.uploader.upload(
                image_bytes,
                public_id=public_id,
                folder="personal_accountant",
                resource_type="image",
                # Apply transformations on upload
                transformation=[
                    # First improve auto-levels
                    {"effect": "improve"},
                    # Then sharpen for text
                    {"effect": "sharpen:100"},
                    # Auto quality
                    {"quality": "auto:best"},
                    # Format auto
                    {"fetch_format": "auto"},
                ],
                # Also keep the original for reference
                eager=[
                    {"quality": "auto:best", "fetch_format": "auto"}
                ],
            )
            
            enhanced_url = result.get("secure_url", result.get("url", ""))
            
            if not enhanced_url:
                raise ImageUploadError("No URL returned from Cloudinary")
            
            # Build the enhanced URL with transformations
            # Use CloudinaryImage to generate transformed URL
            transformed_url = CloudinaryImage(public_id).build_url(
                transformation=[
                    {"effect": "improve"},
                    {"effect": "sharpen:100"},
                    {"quality": "auto:best"},
                ]
            )
            
            enhancements_applied = [
                "auto_improve",
                "sharpen",
                "auto_quality",
            ]
            
            # Determine final quality
            # We assume Cloudinary improves quality somewhat
            # but can't fully fix a poor original
            final_score = min(1.0, pre_score + 0.15)  # Modest improvement
            
            if pre_quality == ImageQuality.POOR:
                # Enhancement helps but still not great
                final_quality = ImageQuality.ACCEPTABLE
                final_issues = pre_issues + [
                    "Image quality improved but may still have issues"
                ]
            else:
                final_quality = ImageQuality.GOOD if final_score >= 0.7 else ImageQuality.ACCEPTABLE
                final_issues = pre_issues
            
            return EnhancedImage(
                upload_id=upload.upload_id,
                enhanced_at=datetime.utcnow(),
                cloudinary_url=transformed_url or enhanced_url,
                quality_assessment=final_quality,
                quality_score=final_score,
                enhancement_applied=enhancements_applied,
                quality_issues=final_issues,
            )
            
        except cloudinary.exceptions.Error as e:
            raise ImageUploadError(f"Cloudinary error: {e}")
        except Exception as e:
            raise ImageUploadError(f"Failed to enhance image: {e}")
    
    def should_proceed_with_ocr(self, enhanced: EnhancedImage) -> tuple[bool, str]:
        """
        Determine if we should proceed with OCR.
        
        Returns: (should_proceed, message_for_user)
        
        DESIGN DECISION: We are conservative here.
        Better to ask user to retake than to OCR garbage.
        """
        min_score = self._app_settings.min_image_quality_score
        
        if enhanced.quality_assessment == ImageQuality.UNUSABLE:
            return False, (
                "❌ This image cannot be processed. "
                "Please take a clearer photo with better lighting."
            )
        
        if enhanced.quality_assessment == ImageQuality.POOR:
            return False, (
                "⚠️ Image quality is too low for reliable text extraction. "
                f"Issues detected: {', '.join(enhanced.quality_issues)}. "
                "Please take a clearer photo."
            )
        
        if enhanced.quality_score < min_score:
            return False, (
                f"⚠️ Image quality score ({enhanced.quality_score:.0%}) is below "
                f"minimum threshold ({min_score:.0%}). "
                "Please take a clearer photo."
            )
        
        # Acceptable or Good
        if enhanced.quality_assessment == ImageQuality.ACCEPTABLE:
            message = (
                "📷 Image quality is acceptable but not ideal. "
                "Extraction will proceed but some details may be missed. "
                f"Tips: {', '.join(enhanced.quality_issues)}" if enhanced.quality_issues else ""
            )
        else:
            message = "✅ Image quality is good. Proceeding with text extraction."
        
        return True, message
