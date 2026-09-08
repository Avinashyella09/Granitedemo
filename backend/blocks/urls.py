from django.urls import path
from .views import (
    BlockListCreateAPIView, 
    BlockDetailAPIView, 
    AssessmentListCreateAPIView, 
    AssessmentDetailAPIView,
    BlockMeasureCVAPIView,
    BlockARMeasureAPIView,
    BlockOverrideAPIView,
    BlockApproveAPIView,
    BlockPDFAPIView,
    BlockAuditLogsAPIView,
    
    # Analytics views
    OfficerAnalyticsAPIView,
    OfficerWeeklyAnalyticsAPIView,
    QuarryComparisonAPIView,
    QuarryTrendAPIView,
    RevenueSummaryAPIView,
    RevenueLeakageAPIView,
    AuditReadinessAPIView,
    AnalyticsAlertsAPIView,
    MapDataAPIView,
    ExecutiveOverviewAPIView
)

urlpatterns = [
    path('blocks/', BlockListCreateAPIView.as_view(), name='block-list-create'),
    path('blocks/ar-measure/', BlockARMeasureAPIView.as_view(), name='block-ar-measure'),
    path('blocks/<str:block_id>/', BlockDetailAPIView.as_view(), name='block-detail'),
    path('blocks/<str:block_id>/measure-cv/', BlockMeasureCVAPIView.as_view(), name='block-measure-cv'),
    path('blocks/<str:block_id>/override/', BlockOverrideAPIView.as_view(), name='block-override'),
    path('blocks/<str:block_id>/approve/', BlockApproveAPIView.as_view(), name='block-approve'),
    path('blocks/<str:block_id>/pdf/', BlockPDFAPIView.as_view(), name='block-pdf'),
    path('blocks/<str:block_id>/audit-logs/', BlockAuditLogsAPIView.as_view(), name='block-audit-logs'),
    path('assessments/', AssessmentListCreateAPIView.as_view(), name='assessment-list-create'),
    path('assessments/<str:block_id>/', AssessmentDetailAPIView.as_view(), name='assessment-detail'),
    
    # Analytics API routes
    path('analytics/officers/', OfficerAnalyticsAPIView.as_view(), name='analytics-officers'),
    path('analytics/officers/<str:officer_id>/weekly/', OfficerWeeklyAnalyticsAPIView.as_view(), name='analytics-officer-weekly'),
    path('analytics/quarries/comparison/', QuarryComparisonAPIView.as_view(), name='analytics-quarries-comparison'),
    path('analytics/quarries/<str:quarry_id>/trend/', QuarryTrendAPIView.as_view(), name='analytics-quarry-trend'),
    path('analytics/revenue/summary/', RevenueSummaryAPIView.as_view(), name='analytics-revenue-summary'),
    path('analytics/revenue/leakage/', RevenueLeakageAPIView.as_view(), name='analytics-revenue-leakage'),
    path('analytics/audit-readiness/', AuditReadinessAPIView.as_view(), name='analytics-audit-readiness'),
    path('analytics/alerts/', AnalyticsAlertsAPIView.as_view(), name='analytics-alerts'),
    path('analytics/map-data/', MapDataAPIView.as_view(), name='analytics-map-data'),
    path('analytics/overview/', ExecutiveOverviewAPIView.as_view(), name='analytics-overview'),
]
