"""
Models for the Jawafdehi accountability platform.

See: .kiro/specs/accountability-platform-core/design.md
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid

from .fields import (
    TextListField,
    TimelineListField,
    EvidenceListField,
)


User = get_user_model()


class JawafEntity(models.Model):
    """
    Represents an entity (person, organization, location, etc.) in the system.

    Entities can either:
    - Reference an entity in the Nepal Entity Service (NES) via nes_id
    - Be custom entities with a display_name (when NES record doesn't exist)
    - Have both nes_id and display_name (display_name is optional)

    nes_id must be unique across all entities (excluding nulls).
    """

    nes_id = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Entity ID from Nepal Entity Service (NES) database (unique)"
    )

    display_name = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        help_text="Display name for the entity (optional if nes_id is present, required otherwise)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jawaf Entity"
        verbose_name_plural = "Jawaf Entities"
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(nes_id__isnull=True, display_name__isnull=True),
                name='jawafentity_must_have_nes_id_or_display_name'
            )
        ]

    def __str__(self):
        if self.nes_id:
            return f"{self.nes_id}" + (f" ({self.display_name})" if self.display_name else "")
        return f"{self.display_name}"

    def clean(self):
        """
        Validate entity data.

        Rules:
        - Must have either nes_id OR display_name (or both)
        - If nes_id is provided, validate it using NES validator
        """
        errors = {}

        # Check that at least one of nes_id or display_name is provided
        has_nes_id = self.nes_id and self.nes_id.strip()
        has_display_name = self.display_name and self.display_name.strip()

        if not has_nes_id and not has_display_name:
            errors['__all__'] = "Entity must have either nes_id or display_name"

        # Validate nes_id format if provided
        if has_nes_id:
            from nes.core.identifiers.validators import validate_entity_id
            try:
                validate_entity_id(self.nes_id)
            except ValueError as e:
                errors['nes_id'] = str(e)

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Override save to validate before saving."""
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        """
        Override delete to prevent deletion if entity is in use.

        Checks if entity is referenced by:
        - Cases (as alleged_entities, related_entities, or locations)
        - DocumentSources (as related_entities, excluding soft-deleted sources)

        Raises ValidationError if entity is in use.
        """
        usage = []

        # Check if used in cases
        alleged_count = self.cases_as_alleged.count()
        if alleged_count > 0:
            usage.append(f"alleged entity in {alleged_count} case(s)")

        related_count = self.cases_as_related.count()
        if related_count > 0:
            usage.append(f"related entity in {related_count} case(s)")

        location_count = self.cases_as_location.count()
        if location_count > 0:
            usage.append(f"location in {location_count} case(s)")

        # Check if used in active document sources (exclude soft-deleted)
        source_count = self.document_sources.filter(is_deleted=False).count()
        if source_count > 0:
            usage.append(f"related entity in {source_count} document source(s)")

        if usage:
            raise ValidationError(
                f"Cannot delete entity '{self}' because it is currently used as: {', '.join(usage)}. "
                f"Remove the entity from all cases and sources before deleting."
            )

        return super().delete(using=using, keep_parents=keep_parents)


class CaseType(models.TextChoices):
    """Enum for case types."""
    CORRUPTION = "CORRUPTION", "Corruption"
    PROMISES = "PROMISES", "Broken Promises"


class CaseState(models.TextChoices):
    """Enum for case states."""
    DRAFT = "DRAFT", "Draft"
    IN_REVIEW = "IN_REVIEW", "In Review"
    PUBLISHED = "PUBLISHED", "Published"
    CLOSED = "CLOSED", "Closed"


class Case(models.Model):
    """
    Core model representing a case of alleged misconduct.

    Supports versioning: multiple Case records can share the same case_id
    to represent different versions/drafts of the same case.
    """

    # Versioning fields
    case_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Unique identifier shared across versions of the same case"
    )
    version = models.IntegerField(
        default=1,
        db_index=True,
        help_text="Version number, increments with each published update"
    )

    # Core fields
    case_type = models.CharField(
        max_length=20,
        choices=CaseType.choices,
        help_text="Type of case (corruption, broken promises, etc.)"
    )
    state = models.CharField(
        max_length=20,
        choices=CaseState.choices,
        default=CaseState.DRAFT,
        db_index=True,
        help_text="Current state in the workflow"
    )
    title = models.CharField(
        max_length=200,
        help_text="Case title"
    )

    # Date fields
    case_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the alleged incident began"
    )
    case_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the alleged incident ended"
    )

    # Entity relationships (many-to-many)
    alleged_entities = models.ManyToManyField(
        JawafEntity,
        blank=True,
        related_name="cases_as_alleged",
        help_text="Entities being accused"
    )
    related_entities = models.ManyToManyField(
        JawafEntity,
        blank=True,
        related_name="cases_as_related",
        help_text="Related entities"
    )
    locations = models.ManyToManyField(
        JawafEntity,
        blank=True,
        related_name="cases_as_location",
        help_text="Location entities"
    )

    # Content fields
    tags = TextListField(
        blank=True,
        help_text="List of tags for categorization"
    )
    description = models.TextField(
        blank=True,
        help_text="Rich text description of the case"
    )
    key_allegations = TextListField(
        blank=True,
        help_text="List of key allegation statements"
    )

    # Structured data fields
    timeline = TimelineListField(
        help_text="List of timeline entries"
    )
    evidence = EvidenceListField(
        help_text="List of evidence entries with source references"
    )

    # Relationships
    contributors = models.ManyToManyField(
        User,
        blank=True,
        related_name="assigned_cases",
        help_text="Contributors assigned to this case"
    )

    # Metadata
    versionInfo = models.JSONField(
        default=dict,
        blank=True,
        help_text="Version metadata tracking changes"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['case_id', 'state', 'version']),
            models.Index(fields=['state', 'version']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.case_id} v{self.version} - {self.title} ({self.state})"

    def save(self, *args, **kwargs):
        """Override save to generate case_id for new cases."""
        if not self.case_id:
            # Generate unique case_id for new cases
            self.case_id = f"case-{uuid.uuid4().hex[:12]}"

        # Validate title is not empty
        if not self.title or not self.title.strip():
            raise ValidationError("Title cannot be empty")

        super().save(*args, **kwargs)

    def validate(self):
        """
        Validate case data based on current state.

        - DRAFT: Lenient validation (only title required)
        - IN_REVIEW/PUBLISHED: Strict validation (all required fields must be complete)
        """
        errors = {}

        # Always require title
        if not self.title or not self.title.strip():
            errors['title'] = "Title is required"

        # Strict validation for IN_REVIEW and PUBLISHED states
        if self.state in [CaseState.IN_REVIEW, CaseState.PUBLISHED]:
            # Require at least one alleged entity for published cases
            if self.alleged_entities.count() == 0:
                errors['alleged_entities'] = "At least one alleged entity is required for IN_REVIEW or PUBLISHED state"

            if not self.key_allegations or len(self.key_allegations) == 0:
                errors['key_allegations'] = "At least one key allegation is required for IN_REVIEW or PUBLISHED state"

            if not self.description or not self.description.strip():
                errors['description'] = "Description is required for IN_REVIEW or PUBLISHED state"

        if errors:
            raise ValidationError(errors)

    def submit(self):
        """
        Submit a draft case for review.

        Transitions state from DRAFT to IN_REVIEW after validation.
        """
        if self.state != CaseState.DRAFT:
            raise ValidationError(f"Can only submit cases in DRAFT state, current state is {self.state}")

        # Validate before submission
        self.state = CaseState.IN_REVIEW
        self.validate()

        # Update versionInfo
        self.versionInfo = {
            'version_number': self.version,
            'action': 'submitted',
            'datetime': timezone.now().isoformat(),
        }

        self.save()

    def create_draft(self):
        """
        Create a new draft version from this case.

        Creates a new Case record with:
        - Same case_id
        - Incremented version
        - State set to DRAFT
        - Copy of all content fields

        Returns the new draft Case instance.
        """
        # Create new draft with incremented version
        draft = Case(
            case_id=self.case_id,
            version=self.version + 1,
            case_type=self.case_type,
            state=CaseState.DRAFT,
            title=self.title,
            case_start_date=self.case_start_date,
            case_end_date=self.case_end_date,
            tags=self.tags.copy() if self.tags else [],
            description=self.description,
            key_allegations=self.key_allegations.copy() if self.key_allegations else [],
            timeline=self.timeline.copy() if self.timeline else [],
            evidence=self.evidence.copy() if self.evidence else [],
            versionInfo={
                'version_number': self.version + 1,
                'action': 'draft_created',
                'source_version': self.version,
                'datetime': timezone.now().isoformat(),
            }
        )

        draft.save()

        # Copy many-to-many relationships
        draft.alleged_entities.set(self.alleged_entities.all())
        draft.related_entities.set(self.related_entities.all())
        draft.locations.set(self.locations.all())
        draft.contributors.set(self.contributors.all())

        return draft

    def publish(self):
        """
        Publish this case.

        Sets state to PUBLISHED and updates versionInfo.
        """
        if self.state not in [CaseState.IN_REVIEW, CaseState.DRAFT]:
            raise ValidationError(f"Can only publish cases in IN_REVIEW or DRAFT state, current state is {self.state}")

        # Validate before publishing
        self.state = CaseState.PUBLISHED
        self.validate()

        # Update versionInfo
        self.versionInfo = {
            'version_number': self.version,
            'action': 'published',
            'datetime': timezone.now().isoformat(),
        }

        self.save()

    def delete(self, using=None, keep_parents=False):
        """
        Soft delete the case by setting state to CLOSED.

        Cases are never hard-deleted to preserve audit history.
        Instead, the state is set to CLOSED and the record remains in the database.
        """
        self.state = CaseState.CLOSED

        # Update versionInfo to track the deletion
        self.versionInfo = {
            'version_number': self.version,
            'action': 'deleted',
            'datetime': timezone.now().isoformat(),
        }

        self.save()

        # Return a tuple (num_deleted, dict) to match Django's delete() signature
        # Since we're soft deleting, we report 0 actual deletions
        return (0, {self._meta.label: 0})


class DocumentSource(models.Model):
    """
    Represents evidence sources that can be referenced by cases.

    Sources are soft-deleted via is_deleted flag to preserve audit history.
    A source is publicly accessible if referenced in evidence of any published case.
    """

    # Unique identifier
    source_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique identifier for the source"
    )

    # Core fields
    title = models.CharField(
        max_length=300,
        help_text="Source title"
    )
    description = models.TextField(
        blank=True,
        help_text="Source description"
    )
    url = models.URLField(
        max_length=2000,
        null=True,
        blank=True,
        help_text="Optional URL to the source"
    )

    # Entity relationships
    related_entities = models.ManyToManyField(
        JawafEntity,
        blank=True,
        related_name="document_sources",
        help_text="Entities related to this source"
    )

    # Contributors (for access control)
    contributors = models.ManyToManyField(
        User,
        blank=True,
        related_name="assigned_sources",
        help_text="Contributors assigned to manage this source"
    )

    # Soft deletion
    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Soft deletion flag"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.source_id} - {self.title}"

    def save(self, *args, **kwargs):
        """Override save to generate source_id and validate required fields."""
        if not self.source_id:
            # Generate unique source_id for new sources
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d")
            self.source_id = f"source:{timestamp}:{uuid.uuid4().hex[:8]}"

        # Validate required fields before saving
        if not self.title or not self.title.strip():
            raise ValidationError("Title is required and cannot be empty")

        super().save(*args, **kwargs)

    def validate(self):
        """
        Validate source data.

        Validates:
        - Title is present and non-empty
        - Entity IDs are valid (validated by EntityListField)
        """
        errors = {}

        # Validate title
        if not self.title or not self.title.strip():
            errors['title'] = "Title is required and cannot be empty"

        if errors:
            raise ValidationError(errors)



class FeedbackType(models.TextChoices):
    """Enum for feedback types."""
    BUG = "bug", "Bug Report"
    FEATURE = "feature", "Feature Request"
    USABILITY = "usability", "Usability Issue"
    CONTENT = "content", "Content Feedback"
    GENERAL = "general", "General Feedback"


class FeedbackStatus(models.TextChoices):
    """Enum for feedback status."""
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In Review"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class Feedback(models.Model):
    """
    Platform feedback submissions from users.

    Stores feedback, bug reports, feature requests, and general comments
    about the Jawafdehi platform.
    """

    # Core fields
    feedback_type = models.CharField(
        max_length=20,
        choices=FeedbackType.choices,
        help_text="Type of feedback"
    )
    subject = models.CharField(
        max_length=200,
        help_text="Brief summary of feedback"
    )
    description = models.TextField(
        max_length=5000,
        help_text="Detailed feedback description"
    )
    related_page = models.CharField(
        max_length=300,
        blank=True,
        help_text="Page or feature related to feedback"
    )

    # Contact information (stored as JSON for flexibility)
    contact_info = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional contact information"
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=FeedbackStatus.choices,
        default=FeedbackStatus.SUBMITTED,
        db_index=True,
        help_text="Current status of feedback"
    )

    # Metadata
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of submitter (for rate limiting)"
    )
    user_agent = models.TextField(
        blank=True,
        help_text="User agent string"
    )

    # Admin notes
    admin_notes = models.TextField(
        blank=True,
        help_text="Internal notes for administrators"
    )

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['feedback_type', 'status']),
            models.Index(fields=['status', 'submitted_at']),
        ]

    def __str__(self):
        return f"{self.feedback_type.upper()}: {self.subject}"
