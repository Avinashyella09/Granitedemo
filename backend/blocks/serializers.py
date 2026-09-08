from rest_framework import serializers
from .models import Quarry, Block, Measurement, Assessment

class QuarrySerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(max_length=255)
    location = serializers.CharField(max_length=500, required=False, allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class MeasurementSerializer(serializers.Serializer):
    length_m = serializers.FloatField(min_value=0.0001)
    breadth_m = serializers.FloatField(min_value=0.0001)
    height_m = serializers.FloatField(min_value=0.0001)
    volume_m3 = serializers.FloatField(min_value=0.0001)
    confidence = serializers.FloatField(default=1.0, required=False, min_value=0.0, max_value=1.0)
    measurement_method = serializers.CharField(default='manual', max_length=50, required=False)
    measured_at = serializers.DateTimeField(required=False)


class BlockSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    block_id = serializers.CharField(max_length=100)
    quarry_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    image_paths = serializers.ListField(child=serializers.CharField(max_length=500), required=False, default=list)
    captured_at = serializers.DateTimeField(required=False, allow_null=True)
    gps_latitude = serializers.FloatField(required=False, allow_null=True)
    gps_longitude = serializers.FloatField(required=False, allow_null=True)
    status = serializers.CharField(default='pending', max_length=50, required=False)
    measurement = MeasurementSerializer(required=False, allow_null=True)
    cv_status = serializers.CharField(read_only=True)
    cv_error_message = serializers.CharField(read_only=True)
    raw_image_path = serializers.CharField(read_only=True)
    annotated_image_path = serializers.CharField(read_only=True)
    is_overridden = serializers.BooleanField(read_only=True)
    original_measurement = MeasurementSerializer(read_only=True)
    override_reason = serializers.CharField(read_only=True)
    approval_status = serializers.CharField(read_only=True)
    approved_by = serializers.CharField(read_only=True)
    approved_at = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_block_id(self, value):
        # We clean and normalize block_id
        return value.strip()


class ImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField(required=True)



class AssessmentSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    block_id = serializers.CharField(max_length=100)
    granite_category = serializers.CharField(max_length=100)
    gangsaw_classification = serializers.CharField(max_length=100)
    volume_m3 = serializers.FloatField(read_only=True)
    weight_mt = serializers.FloatField(read_only=True)
    rate_per_mt = serializers.FloatField(read_only=True)
    indicative_seigniorage = serializers.FloatField(read_only=True)
    density = serializers.FloatField(required=False, write_only=True, min_value=0.0001)
    density_mt_per_m3 = serializers.FloatField(read_only=True)
    status = serializers.CharField(default='draft', max_length=50, required=False)
    created_at = serializers.DateTimeField(read_only=True)

