import datetime
import os
from io import BytesIO
from django.conf import settings
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def generate_block_pdf(block, assessment=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter,
        rightMargin=36, 
        leftMargin=36, 
        topMargin=36, 
        bottomMargin=36
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'), # Slate 900
        spaceAfter=15
    )
    
    section_style = ParagraphStyle(
        'DocSection',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#1E3A8A'), # Navy/Blue 900
        spaceBefore=10,
        spaceAfter=8
    )

    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#334155') # Slate 700
    )

    header_style = ParagraphStyle(
        'DocHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.white
    )


    small_style = ParagraphStyle(
        'DocSmall', parent=styles['Normal'], fontName='Helvetica', fontSize=8,
        leading=11, textColor=colors.HexColor('#64748B')
    )

    # POLICY CHANGE. This previously raised when there was no assessment, and
    # the view turned that into a 409 - so a measured-but-unpriced block could
    # not be exported at all. Every registered block is now exportable.
    #
    # The refusal existed to stop a report showing blanks where the money should
    # be, and that concern still stands: the answer is not to print a zero or an
    # empty cell, but to say plainly that no seigniorage has been determined.
    # An unassessed block therefore produces a MEASUREMENT RECORD - same
    # measurement, approval, audit, location and verification sections, with
    # every financial field reading "Not assessed" and the document titled and
    # disclaimed accordingly. Nothing is fabricated and nothing is implied.
    assessed = assessment is not None

    measurement = block.measurement
    if measurement is None:
        raise ValueError(f"Block '{block.block_id}' has no measurement.")

    # --- Traceability sourced from persisted audit records ------------------
    from .models import AuditLog
    from . import report_hash

    def audit_for(action_prefix):
        return AuditLog.objects(
            block=block, action__startswith=action_prefix
        ).order_by('-timestamp').first()

    receipt_audit = audit_for('ar_measurement_submitted')
    assessed_audit = audit_for('seigniorage_assessed')
    approval_audit = audit_for('block_approval')

    receipt_id = str(receipt_audit.id) if receipt_audit else None
    from . import report_basis
    schedule_version, schedule_source = report_basis.schedule_provenance(assessed_audit)
    report_no = report_hash.report_number(block, assessment)
    verification = report_hash.verification_hash(block, assessment, receipt_id)
    generated_at = datetime.datetime.utcnow()

    def kv_table(rows, widths=(170, 370)):
        table = Table(rows, colWidths=list(widths))
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return table

    # --- Letterhead ---------------------------------------------------------
    story.append(Paragraph("GOVERNMENT OF ANDHRA PRADESH", ParagraphStyle(
        'GovHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8,
        leading=10, textColor=colors.HexColor('#475569'), alignment=1)))
    story.append(Paragraph("DEPARTMENT OF MINES & GEOLOGY", ParagraphStyle(
        'GovSubHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10,
        leading=12, textColor=colors.HexColor('#0F172A'), alignment=1, spaceAfter=10)))
    story.append(Paragraph(
        "Granite Block Measurement &amp; Seigniorage Assessment Report" if assessed
        else "Granite Block Measurement Record (Not Yet Assessed)", title_style))
    story.append(Paragraph(
        f"Report No: <b>{report_no}</b> &nbsp;|&nbsp; Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        body_style))
    story.append(Spacer(1, 12))

    # --- 1. Block identification --------------------------------------------
    story.append(Paragraph("1. Block Identification", section_style))
    quarry_ref = block.submitted_quarry_id or (block.quarry.id if block.quarry else None)
    quarry_status = ("Resolved" if block.quarry else
                     ("Unresolved in current reference data" if quarry_ref else "Not supplied"))
    officer_ref = block.inspecting_officer_id
    officer_resolved = False
    if officer_ref:
        from .models import Officer
        officer_resolved = Officer.objects(officer_id=officer_ref).first() is not None
    officer_status = ("Resolved" if officer_resolved else
                      ("Unresolved in current reference data" if officer_ref else "Not supplied"))

    story.append(kv_table([
        [Paragraph("Field", header_style), Paragraph("Value", header_style)],
        [Paragraph("Block ID", body_style), Paragraph(block.block_id, body_style)],
        [Paragraph("Quarry reference", body_style),
         Paragraph(f"{quarry_ref or 'Not supplied'}<br/><font size=8 color='#64748B'>Reference status: {quarry_status}</font>", body_style)],
        [Paragraph("Officer reference", body_style),
         Paragraph(f"{officer_ref or 'Not supplied'}<br/><font size=8 color='#64748B'>Reference status: {officer_status}</font>", body_style)],
        [Paragraph("Submission receipt ID", body_style), Paragraph(receipt_id or "Not recorded", body_style)],
        [Paragraph("Measurement method", body_style),
         Paragraph((measurement.measurement_method or 'unknown').upper(), body_style)],
        [Paragraph("CV inference status", body_style),
         Paragraph((block.cv_status or 'unknown').replace('_', ' ').upper(), body_style)],
        [Paragraph("Capture timestamp", body_style),
         Paragraph(block.captured_at.strftime('%Y-%m-%d %H:%M:%S') + " UTC" if block.captured_at else "Not recorded", body_style)],
    ]))
    story.append(Spacer(1, 10))

    # --- 2. Measurement ------------------------------------------------------
    story.append(Paragraph("2. Measured Dimensions", section_style))
    story.append(kv_table([
        [Paragraph("Dimension", header_style), Paragraph("Value", header_style)],
        [Paragraph("Length", body_style), Paragraph(f"{measurement.length_m:.4f} m", body_style)],
        [Paragraph("Breadth", body_style), Paragraph(f"{measurement.breadth_m:.4f} m", body_style)],
        [Paragraph("Height", body_style), Paragraph(f"{measurement.height_m:.4f} m", body_style)],
        # Sourced from the Block measurement record, which is the measurement
        # source of truth and holds the unrounded product. Section 4 shows how
        # that volume feeds the money.
        [Paragraph("Volume (L x B x H)", body_style),
         Paragraph(f"{measurement.volume_m3:.6f} m&sup3;", body_style)],
    ]))
    story.append(Spacer(1, 10))

    # --- 3. Gangsaw classification ------------------------------------------
    story.append(Paragraph("3. Size Classification", section_style))
    length_cm = measurement.length_m * 100
    breadth_cm = measurement.breadth_m * 100
    story.append(Paragraph(
        "Determined by a server-side rule applied to the measured dimensions. "
        "This is a deterministic threshold comparison, not a machine-learning prediction.",
        small_style))
    story.append(Spacer(1, 4))
    story.append(kv_table([
        [Paragraph("Item", header_style), Paragraph("Value", header_style)],
        [Paragraph("Rule applied", body_style),
         Paragraph("Length &gt; 270 cm AND Breadth &gt; 150 cm &rarr; Above Gangsaw; otherwise Within Gangsaw", body_style)],
        [Paragraph("Measured (cm)", body_style),
         Paragraph(f"{length_cm:.2f} cm x {breadth_cm:.2f} cm", body_style)],
        [Paragraph("Comparison", body_style),
         Paragraph(f"{length_cm:.2f} &gt; 270 = {'yes' if length_cm > 270 else 'no'}; "
                   f"{breadth_cm:.2f} &gt; 150 = {'yes' if breadth_cm > 150 else 'no'}", body_style)],
        [Paragraph("Classification", body_style),
         Paragraph(f"<b>{assessment.gangsaw_classification}</b>" if assessed
                   else "<b>Not determined</b> &mdash; classification is recorded when the "
                        "block is assessed", body_style)],
    ]))
    story.append(Spacer(1, 10))

    # --- 4. Seigniorage ------------------------------------------------------
    #
    # PRESENTATION FIX. This section previously computed
    #     assessment.volume_m3 * assessment.density_mt_per_m3
    # and printed it under the label "Tonnage (used for amount)". That product
    # uses the 6-dp ROUNDED stored volume, not the unrounded one the amount was
    # actually derived from, and the two do not reconcile to the same paisa: for
    # the real block2 record the rounded basis implies INR 45.86 against the
    # correctly stored INR 45.85. A verifier following the report's own
    # arithmetic would therefore have judged the assessment wrong.
    #
    # The exact basis now comes from report_basis, which reuses the engine's own
    # formula, and the rounded stored values are labelled as presentation only.
    # No monetary value is recomputed here - the payable figure is read straight
    # from the persisted Assessment, as it always was.
    story.append(Paragraph("4. Seigniorage Calculation", section_style))

    if not assessed:
        # No assessment exists, so there is no rate, no tonnage and no payable
        # amount. Stating that is the whole point - a zero here would be a
        # fabricated figure, and a blank would look like an omission.
        story.append(kv_table([
            [Paragraph("Item", header_style), Paragraph("Value", header_style)],
            [Paragraph("Granite category", body_style), Paragraph("Not assessed", body_style)],
            [Paragraph("Classification", body_style), Paragraph("Not assessed", body_style)],
            [Paragraph("Density", body_style), Paragraph("Not assessed", body_style)],
            [Paragraph("Tonnage", body_style), Paragraph("Not assessed", body_style)],
            [Paragraph("Official rate", body_style), Paragraph("Not assessed", body_style)],
            [Paragraph("Seigniorage payable", body_style),
             Paragraph("<b>NOT ASSESSED</b>", body_style)],
        ]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "No seigniorage assessment has been created for this block, so no rate, tonnage or "
            "payable amount has been determined. This document records the measurement only. "
            "Running the official assessment produces the payable figure and a new report number.",
            small_style))
        story.append(Spacer(1, 10))
    else:
        basis = report_basis.basis_for(block, assessment)

        calc_rows = [
            [Paragraph("Item", header_style), Paragraph("Value", header_style)],
            [Paragraph("Granite category", body_style), Paragraph(assessment.granite_category, body_style)],
            [Paragraph("Classification", body_style), Paragraph(assessment.gangsaw_classification, body_style)],
            [Paragraph("Density", body_style), Paragraph(f"{assessment.density_mt_per_m3} MT/m&sup3;", body_style)],
        ]

        if basis["status"] == report_basis.REPRODUCED:
            calc_rows += [
                [Paragraph("Volume &mdash; exact basis", body_style),
                 Paragraph(f"{report_basis.decimal_text(basis['exact_volume_m3'])} m&sup3;", body_style)],
                [Paragraph("Volume &mdash; stored (6 dp)", body_style),
                 Paragraph(f"{assessment.volume_m3:.6f} m&sup3;<br/>"
                           f"<font size=8 color='#64748B'>Rounded for presentation.</font>", body_style)],
                [Paragraph("Tonnage &mdash; exact basis used for the amount", body_style),
                 Paragraph(f"<b>{report_basis.decimal_text(basis['exact_tonnage_mt'])} MT</b>", body_style)],
                [Paragraph("Tonnage &mdash; stored (3 dp)", body_style),
                 Paragraph(f"{assessment.weight_mt:.3f} MT<br/>"
                           f"<font size=8 color='#64748B'>Rounded for presentation.</font>", body_style)],
            ]
        else:
            calc_rows += [
                [Paragraph("Volume &mdash; stored (6 dp)", body_style),
                 Paragraph(f"{assessment.volume_m3:.6f} m&sup3;", body_style)],
                [Paragraph("Tonnage &mdash; stored (3 dp)", body_style),
                 Paragraph(f"{assessment.weight_mt:.3f} MT", body_style)],
                [Paragraph("Tonnage &mdash; exact basis", body_style),
                 Paragraph("Not restatable &mdash; see note below", body_style)],
            ]

        calc_rows += [
            [Paragraph("Official rate", body_style), Paragraph(f"INR {assessment.rate_per_mt:,.2f} per MT", body_style)],
            [Paragraph("Seigniorage payable (as assessed and stored)", body_style),
             Paragraph(f"<b>INR {assessment.indicative_seigniorage:,.2f}</b>", body_style)],
        ]
        story.append(kv_table(calc_rows))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Volume = L x B x H &nbsp;|&nbsp; Tonnage = Volume x Density &nbsp;|&nbsp; Seigniorage = Tonnage x Rate",
            small_style))
        story.append(Spacer(1, 3))
        story.append(Paragraph(basis["explanation"], small_style))
    story.append(Spacer(1, 10))

    # --- 5. Official schedule ------------------------------------------------
    story.append(Paragraph("5. Applicable Official Schedule", section_style))
    story.append(kv_table([
        [Paragraph("Item", header_style), Paragraph("Value", header_style)],
        # Taken from what the assessment audit actually recorded, not from the
        # engine's current constant: naming today's schedule on a historical
        # assessment would assert a tariff that may not be the one applied.
        [Paragraph("Rate schedule", body_style),
         Paragraph(
             ("Not applicable &mdash; no assessment has been run" if not assessed
              else f"{schedule_version}"
                   + ("" if schedule_source == report_basis.RECORDED_AT_ASSESSMENT
                      else "<br/><font size=8 color='#64748B'>Current configured schedule; "
                           "no schedule version was recorded with this assessment.</font>")),
             body_style)],
        [Paragraph("Applicable entry", body_style),
         Paragraph(f"{assessment.granite_category} &mdash; {assessment.gangsaw_classification} "
                   f"&mdash; INR {assessment.rate_per_mt:,.2f}/MT" if assessed
                   else "Not applicable &mdash; no assessment has been run, so no schedule "
                        "entry has been applied", body_style)],
    ]))
    story.append(Spacer(1, 10))

    # --- 6. Approval ---------------------------------------------------------
    story.append(Paragraph("6. Supervisor Approval", section_style))
    approval_reason = ""
    if approval_audit and approval_audit.details and "reason=" in approval_audit.details:
        approval_reason = approval_audit.details.split("reason=", 1)[1].strip()
    story.append(kv_table([
        [Paragraph("Item", header_style), Paragraph("Value", header_style)],
        [Paragraph("Approval status", body_style),
         Paragraph((block.approval_status or 'pending').upper(), body_style)],
        [Paragraph("Approved by", body_style),
         Paragraph(f"{block.approved_by or 'Not approved'}"
                   f"{'<br/><font size=8 color=\'#64748B\'>Authenticated backend identity</font>' if block.approved_by else ''}", body_style)],
        [Paragraph("Approval timestamp", body_style),
         Paragraph(block.approved_at.strftime('%Y-%m-%d %H:%M:%S') + " UTC" if block.approved_at else "Not approved", body_style)],
        [Paragraph("Reason recorded", body_style), Paragraph(approval_reason or "Not recorded", body_style)],
    ]))
    story.append(Spacer(1, 10))

    # --- 7. Traceability -----------------------------------------------------
    story.append(Paragraph("7. Audit Traceability", section_style))
    def audit_row(label, entry):
        if not entry:
            return [Paragraph(label, body_style), Paragraph("Not recorded", body_style)]
        return [Paragraph(label, body_style),
                Paragraph(f"ref {entry.id} &mdash; actor: {entry.actor} &mdash; "
                          f"{entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC", body_style)]
    story.append(kv_table([
        [Paragraph("Event", header_style), Paragraph("Reference", header_style)],
        audit_row("Field measurement submitted", receipt_audit),
        audit_row("Seigniorage assessed", assessed_audit),
        audit_row("Supervisor approval", approval_audit),
    ]))
    story.append(Spacer(1, 10))

    # --- 8. Location ---------------------------------------------------------
    story.append(Paragraph("8. Capture Location", section_style))
    if block.gps_latitude is not None and block.gps_longitude is not None:
        story.append(kv_table([
            [Paragraph("Item", header_style), Paragraph("Value", header_style)],
            [Paragraph("Latitude", body_style), Paragraph(f"{block.gps_latitude:.6f}", body_style)],
            [Paragraph("Longitude", body_style), Paragraph(f"{block.gps_longitude:.6f}", body_style)],
        ]))
    else:
        story.append(Paragraph("<b>GPS STATUS: NOT CAPTURED</b>", body_style))
        story.append(Paragraph(
            "No coordinates were recorded with this submission. No location has been inferred or substituted.",
            small_style))
    story.append(Spacer(1, 10))

    # --- 9. Field image ------------------------------------------------------
    # Heading reflects what the image actually is. The previous template titled
    # this "Visual Evidence (Annotated Output)" even for AR submissions where no
    # CV inference ever ran and no annotated image exists.
    has_annotated = bool(block.annotated_image_path)
    story.append(Paragraph(
        "9. Visual Evidence (CV Annotated Output)" if has_annotated else "9. Submitted Field Image",
        section_style))
    image_rendered = False
    candidate = block.annotated_image_path or block.raw_image_path
    if candidate:
        img_full_path = os.path.join(settings.MEDIA_ROOT, candidate)
        if os.path.exists(img_full_path):
            try:
                story.append(Image(img_full_path, width=360, height=270))
                image_rendered = True
            except Exception as e:
                story.append(Paragraph(f"[Image could not be rendered: {e}]", body_style))
    if not image_rendered:
        story.append(Paragraph("No field image available.", body_style))
    if not has_annotated:
        story.append(Paragraph(
            "Field capture image as submitted. No computer-vision annotation was produced for this record.",
            small_style))
    story.append(Spacer(1, 12))

    # --- 10. Verification + disclaimer ---------------------------------------
    story.append(Paragraph("10. Verification", section_style))
    story.append(kv_table([
        [Paragraph("Item", header_style), Paragraph("Value", header_style)],
        [Paragraph("Report number", body_style), Paragraph(report_no, body_style)],
        [Paragraph("Submission receipt ID", body_style), Paragraph(receipt_id or "Not recorded", body_style)],
        [Paragraph("Generated at", body_style),
         Paragraph(generated_at.strftime('%Y-%m-%d %H:%M:%S') + " UTC", body_style)],
        [Paragraph("Verification SHA-256", body_style),
         Paragraph(f"<font face='Courier' size=8>{verification}</font>", body_style)],
    ]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "The verification digest is computed from the stored business record (block identity, measured "
        "dimensions, volume, category, classification, density, tonnage, rate, amount, approval state, "
        "approver and receipt reference) - not from the PDF file bytes. Recomputing it from the database "
        "record reproduces the same value, so an altered record will not match a printed report.",
        small_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Disclaimer", section_style))
    if not assessed:
        story.append(Paragraph(
            "<b>THIS IS A MEASUREMENT RECORD, NOT A SEIGNIORAGE ASSESSMENT.</b> No assessment has been "
            "created for this block, so no granite category, size classification, tonnage, rate or payable "
            "amount has been determined, and none is stated above. This document must not be used as a "
            "basis for any demand or payment.",
            small_style))
        story.append(Spacer(1, 6))
    story.append(Paragraph(
        "This report is generated by the RTGS proof-of-concept system from the recorded field measurement, "
        "the configured official rate schedule and the stored approval and audit records. It is intended for "
        "demonstration and validation of the workflow. It is not, by itself, a legal transport permit, a final "
        "statutory demand, or a substitute for the competent authority's official record.",
        small_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
