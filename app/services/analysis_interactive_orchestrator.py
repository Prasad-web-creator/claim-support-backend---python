from typing import Optional
from datetime import datetime
from fastapi import BackgroundTasks
import time
import asyncio
from beanie import PydanticObjectId as ObjectId
import json
from app.core.logging import logger
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession, ClarificationQuestion, ClarificationAnswer
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.extraction.document_extraction import extract_text_from_document
from app.services.extraction.text_cleaning import clean_text
from app.services.extraction.policy_extraction import extract_policy_details
from app.services.extraction.prescription_extraction import extract_prescription_details
from app.services.extraction.json_validation import validate_extracted_json
from app.services.coverage.business_rules import enforce_business_rules
from app.services.coverage.coverage_analysis import analyze_coverage
from app.services.coverage.report_generator import generate_report
from app.services.llm.ai_client import AnalysisCostTracker, set_current_cost_tracker, get_current_cost_tracker
from app.services.analysis_orchestrator import _fetch_file_bytes_by_gridfs_id, _background_post_processing


def _parse_and_format_date(date_val):
    if not date_val:
        return None, None
    parsed = None
    if isinstance(date_val, datetime):
        parsed = date_val.replace(tzinfo=None)
    else:
        try:
            from dateutil import parser
            parsed = parser.parse(str(date_val)).replace(tzinfo=None)
        except Exception:
            pass
        if not parsed:
            clean_str = str(date_val).strip()
            for fmt in (
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%m/%d/%Y",
                "%Y/%m/%d",
                "%d.%m.%Y",
                "%d-%b-%Y",
                "%d-%B-%Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
            ):
                try:
                    parsed = datetime.strptime(clean_str.split("T")[0] if "T" not in fmt and "T" in clean_str else clean_str, fmt)
                    break
                except Exception:
                    continue
    if parsed:
        return parsed, parsed.strftime("%d-%m-%Y")
    return None, None

async def start_analysis_session(
    user_id: str,
    prescription_id: str,
    policy_file_id: Optional[str] = None,
    policy_doc_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None
) -> dict:
    start_time = time.time()
    logger.info(f"[Interactive Orchestrator] Starting session for User: {user_id}")

    session = AnalysisSession(
        user_id=user_id,
        policy_id=policy_doc_id or policy_file_id,
        prescription_id=prescription_id,
        status="extracting"
    )
    await session.insert()

    tracker = AnalysisCostTracker(session_id=str(session.id))
    set_current_cost_tracker(tracker)

    try:
        async def process_document(doc_id, doc_label="Document"):
            logger.info(f"[Interactive Orchestrator] [DEBUG] Fetching file bytes from GridFS for {doc_label} (ID: {doc_id})...")
            bytes_data, mime = await _fetch_file_bytes_by_gridfs_id(doc_id, user_id)
            raw_text = await extract_text_from_document(bytes_data, mime, doc_label=doc_label)
            return clean_text(raw_text)

        # Check if prescription_id refers to a Prescription document or manual prescription
        is_manual_rx = False
        manual_rx_text = None
        rx_doc = None
        target_rx_file_id = prescription_id

        if prescription_id:
            try:
                if len(str(prescription_id)) == 24:
                    rx_doc = await Prescription.get(ObjectId(prescription_id))
            except Exception:
                rx_doc = None

        if rx_doc:
            if rx_doc.is_manual:
                is_manual_rx = True
                manual_rx_text = rx_doc.extracted_prescription_text or ""
            elif rx_doc.grid_fs_file_id:
                target_rx_file_id = rx_doc.grid_fs_file_id

        async def load_rx_text():
            if is_manual_rx and manual_rx_text:
                logger.info("[Interactive Orchestrator] [DEBUG] Prescription is self-entered manual text by user.")
                return clean_text(manual_rx_text)
            try:
                return await process_document(target_rx_file_id, doc_label="Prescription Document")
            except Exception as e:
                # If target_rx_file_id is raw manual text directly passed
                if prescription_id and len(prescription_id) > 10 and not prescription_id.isalnum():
                    return clean_text(prescription_id)
                raise e

        policy_text = ""
        existing_policy_json = None
        
        if policy_doc_id:
            policy_doc = await Policy.get(ObjectId(policy_doc_id))
            if not policy_doc or policy_doc.user_id != user_id:
                raise ValueError("Invalid Policy ID or unauthorized")
            if policy_doc.extracted_policy_text:
                logger.info(
                    f"[Interactive Orchestrator] [DEBUG] [Policy Document] Reusing CACHED text ({len(policy_doc.extracted_policy_text)} chars) on Policy {policy_doc.id}"
                )
                policy_text = policy_doc.extracted_policy_text
                existing_policy_json = policy_doc.extracted_policy_json or {}
                rx_text = await load_rx_text()
            else:
                logger.info("[Interactive Orchestrator] [DEBUG] Concurrently extracting Policy and Prescription documents...")
                policy_text, rx_text = await asyncio.gather(
                    process_document(policy_doc.grid_fs_file_id, doc_label="Policy Document"),
                    load_rx_text()
                )
        else:
            logger.info("[Interactive Orchestrator] [DEBUG] Concurrently extracting Policy and Prescription documents...")
            policy_text, rx_text = await asyncio.gather(
                process_document(policy_file_id, doc_label="Policy Document"),
                load_rx_text()
            )

        session.policy_text = policy_text
        session.prescription_text = rx_text
        
        if existing_policy_json:
            policy_data = {"extractedJson": existing_policy_json}
            rx_data = await extract_prescription_details(rx_text)
        else:
            policy_data, rx_data = await asyncio.gather(
                extract_policy_details(policy_text),
                extract_prescription_details(rx_text)
            )

        validation = validate_extracted_json(
            policy_data.get("extractedJson"),
            rx_data.get("extractedJson")
        )
        
        policy_json = validation["validatedPolicyJson"]
        prescription_json = validation["validatedPrescriptionJson"]

        if is_manual_rx:
            from datetime import datetime
            today_str = datetime.now().strftime("%Y-%m-%d")
            prescription_json["isManual"] = True
            prescription_json["prescriptionSource"] = "Self-entered Prescription"
            prescription_json["manualText"] = rx_text
            if not prescription_json.get("visitDate"):
                prescription_json["visitDate"] = today_str
            if not prescription_json.get("consultationDate"):
                prescription_json["consultationDate"] = today_str

        # Always persist extracted text and JSON on documents in MongoDB for instant reuse
        if rx_doc:
            rx_doc.extracted_prescription_text = rx_text or ""
            rx_doc.extracted_prescription_json = prescription_json or {}
            if is_manual_rx:
                rx_doc.is_manual = True
            if prescription_json.get("patientName"):
                rx_doc.patient_name = str(prescription_json.get("patientName"))
            if prescription_json.get("doctorName"):
                rx_doc.doctor_name = str(prescription_json.get("doctorName"))
            if prescription_json.get("hospitalName"):
                rx_doc.hospital_name = str(prescription_json.get("hospitalName"))
            if prescription_json.get("diagnosis"):
                rx_doc.diagnosis = str(prescription_json.get("diagnosis"))
            if prescription_json.get("prescriptionNumber"):
                rx_doc.prescription_number = str(prescription_json.get("prescriptionNumber"))
            if prescription_json.get("visitDate") or prescription_json.get("consultationDate"):
                rx_doc.visit_date = prescription_json.get("visitDate") or prescription_json.get("consultationDate")
            rx_doc.processing_status = "completed"
            await rx_doc.save()
            logger.info(f"[Interactive Orchestrator] Saved extracted prescription text & JSON to Prescription {rx_doc.id}")
        elif target_rx_file_id:
            try:
                new_rx_doc = Prescription(
                    user_id=user_id,
                    hospital_name=str(prescription_json.get("hospitalName") or ""),
                    doctor_name=str(prescription_json.get("doctorName") or ""),
                    patient_name=str(prescription_json.get("patientName") or ""),
                    prescription_number=str(prescription_json.get("prescriptionNumber") or ""),
                    visit_date=prescription_json.get("visitDate") or prescription_json.get("consultationDate") or "",
                    diagnosis=str(prescription_json.get("diagnosis") or ""),
                    is_manual=is_manual_rx,
                    grid_fs_file_id="" if is_manual_rx else str(target_rx_file_id),
                    original_file_name=session.prescription_id or "",
                    extracted_prescription_text=rx_text or "",
                    extracted_prescription_json=prescription_json or {},
                    processing_status="completed"
                )
                await new_rx_doc.insert()
                session.prescription_id = str(new_rx_doc.id)
                rx_doc = new_rx_doc
                logger.info(f"[Interactive Orchestrator] Created and persisted new Prescription {new_rx_doc.id}")
            except Exception as rx_err:
                logger.warning(f"[Interactive Orchestrator] Could not create new Prescription record: {rx_err}")

        if policy_doc and (not policy_doc.extracted_policy_text or not policy_doc.extracted_policy_json):
            policy_doc.extracted_policy_text = policy_text
            policy_doc.extracted_policy_json = policy_json
            if policy_json.get("policyHolder") and not policy_doc.policy_holder_name:
                policy_doc.policy_holder_name = policy_json.get("policyHolder")
            await policy_doc.save()
            logger.info(f"[Interactive Orchestrator] Cached extracted policy text & JSON on Policy {policy_doc.id}")

        session.policy_json = policy_json
        session.prescription_json = prescription_json

        # Early check for document validity
        is_policy_valid = validation.get("isPolicyValid", True)
        is_rx_valid = validation.get("isPrescriptionValid", True)
        
        if not is_policy_valid or not is_rx_valid:
            logger.warning(f"[Interactive Orchestrator] Document validation failed: policyValid={is_policy_valid}, prescriptionValid={is_rx_valid}")
            p_reason = ""
            rx_reason = ""
            if not is_policy_valid:
                p_reason = "The uploaded policy document contains no recognizable insurance policy clauses, covered treatments, benefit rules, or insurance terms."
            if not is_rx_valid:
                if is_manual_rx:
                    rx_reason = "The self-entered text contains no recognizable medical details (no diagnosis, symptoms, diseases, medicines, or medical tests)."
                else:
                    rx_reason = "The uploaded document contains no valid diagnosis, medicines, medical tests, procedures, or symptoms."

            if not is_policy_valid and not is_rx_valid:
                inv_status = "Invalid Policy and Prescription"
            elif not is_policy_valid:
                inv_status = "Invalid Policy"
            else:
                inv_status = "Invalid Prescription"

            extraction_time_ms = int((time.time() - start_time) * 1000)
            session.extraction_time_ms = extraction_time_ms
            session.accumulated_processing_time_ms = extraction_time_ms
            session.status = "completed"
            await session.save()

            final_report = generate_report(
                policy_json=policy_json,
                prescription_json=prescription_json,
                business_rule_results={},
                coverage_analysis={
                    "documentValidity": {
                        "policyValid": is_policy_valid,
                        "prescriptionValid": is_rx_valid,
                        "isPolicyValid": is_policy_valid,
                        "isPrescriptionValid": is_rx_valid,
                        "policyInvalidReason": p_reason,
                        "prescriptionInvalidReason": rx_reason,
                        "errors": validation.get("errors", [])
                    },
                    "overallStatus": inv_status,
                    "comparison": []
                },
                processing_time_ms=extraction_time_ms
            )

            # Do NOT store invalid coverage summary report in DB as per user requirement
            final_report["status"] = "complete"
            return final_report

        # Check for policy start date conflict between user manual input and document extraction
        user_entered_start_date = None
        if policy_doc and policy_doc.policy_start_date:
            user_entered_start_date = policy_doc.policy_start_date
        elif policy_file_id:
            try:
                p_by_file = await Policy.find_one(Policy.grid_fs_file_id == policy_file_id, Policy.user_id == user_id)
                if p_by_file and p_by_file.policy_start_date:
                    user_entered_start_date = p_by_file.policy_start_date
            except Exception:
                pass

        user_dt, user_disp = _parse_and_format_date(user_entered_start_date)
        doc_dt, doc_disp = _parse_and_format_date(policy_json.get("policyStartDate"))

        if user_dt and doc_dt and user_dt.date() != doc_dt.date():
            logger.info(f"[Interactive Orchestrator] Policy start date conflict: user entered {user_disp}, doc extracted {doc_disp}")
            
            question_data = {
                "id": "policy_start_date_conflict",
                "category": "eligibility",
                "title": "Policy Start Date Conflict",
                "question": f"Policy mentioned start date is {doc_disp}, but you mentioned {user_disp}. Can I proceed with your option?",
                "type": "single_choice",
                "required": True,
                "options": [
                    f"Use my entered date ({user_disp})",
                    f"Use policy document date ({doc_disp})"
                ],
                "reason": "A conflict was found between the start date you entered and the start date extracted from the uploaded policy document."
            }

            session.questions.append(ClarificationQuestion(
                id=question_data["id"],
                title=question_data["title"],
                question=question_data["question"],
                type=question_data["type"],
                category=question_data["category"],
                required=question_data["required"],
                options=question_data["options"],
                reason=question_data["reason"]
            ))

            session.status = "waiting_for_user"
            await session.save()

            return {
                "status": "needs_clarification",
                "sessionId": str(session.id),
                "questions": [question_data]
            }
        elif user_dt and not doc_dt:
            policy_json["policyStartDate"] = user_dt.strftime("%Y-%m-%d")
        elif user_dt and doc_dt and user_dt.date() == doc_dt.date():
            policy_json["policyStartDate"] = user_dt.strftime("%Y-%m-%d")

        br_results = enforce_business_rules(policy_json, prescription_json)
        session.business_rules = br_results

        # Track extraction & business rule processing time
        extraction_time_ms = int((time.time() - start_time) * 1000)
        session.extraction_time_ms = extraction_time_ms
        session.accumulated_processing_time_ms = extraction_time_ms
        session.status = "analyzing"
        await session.save()

        return await _run_llm_analysis(session, policy_json, prescription_json, br_results, background_tasks)

    except Exception as e:
        logger.error(f"[Interactive Orchestrator] Session {session.id} failed: {e}")
        session.status = "failed"
        await session.save()
        raise e

async def _run_llm_analysis(session: AnalysisSession, policy_json: dict, prescription_json: dict, br_results: dict, background_tasks: Optional[BackgroundTasks]) -> dict:
    from app.models.analysis_audit_log import AnalysisAuditLog
    llm_start_time = time.time()
    
    session.round_count += 1
    await session.save()

    if session.round_count > 3:
        logger.warning(f"[Interactive Orchestrator] Session {session.id} exceeded max rounds. Forcing manual review.")
        session.status = "manual_review_required"
        await session.save()
        return {"status": "manual_review_required", "reason": "Maximum clarification rounds exceeded."}

    # Format clarification history — match by both field name and alias
    clarification_history = []
    for q in session.questions:
        # Support both `question_id` and `questionId` field name due to Pydantic alias
        answer_obj = next(
            (a for a in session.answers if getattr(a, 'question_id', None) == q.id or getattr(a, 'questionId', None) == q.id),
            None
        )
        if answer_obj:
            clarification_history.append({
                "Question_ID": q.id,
                "Title": q.title,
                "Question": q.question,
                "User_Answer": answer_obj.answer
            })
    
    logger.info(f"[Interactive Orchestrator] Round {session.round_count}: clarification_history={clarification_history}")

    coverage_analysis = await analyze_coverage(
        policy_text=session.policy_text or "",
        prescription_text=session.prescription_text or "",
        business_rule_results=br_results,
        prescription_json=prescription_json,
        clarification_history=clarification_history
    )

    llm_round_duration_ms = int((time.time() - llm_start_time) * 1000)
    extraction_time = getattr(session, "extraction_time_ms", None) or session.accumulated_processing_time_ms or 0
    total_processing_time = extraction_time + llm_round_duration_ms

    next_action = coverage_analysis.get("next_action", "manual_review")
    confidence = coverage_analysis.get("confidence", 0)
    
    # Audit log
    audit_log = AnalysisAuditLog(
        session_id=str(session.id),
        analysis_round=session.round_count,
        prompt_version="2.1.0",
        prescription_id=session.prescription_id,
        policy_id=session.policy_id,
        clarification_context={"history": clarification_history},
        llm_response=coverage_analysis,
        confidence_score=confidence,
        final_decision=next_action
    )

    if next_action == "ask_questions":
        new_questions = coverage_analysis.get("questions", [])
        filtered_questions = []
        for q_data in new_questions:
            q_id = q_data.get("id")
            
            # Validation
            if not q_id or not q_data.get("title") or not q_data.get("question"):
                continue
            
            # Deduplication
            if not any(q.id == q_id for q in session.questions):
                if len(session.questions) + len(filtered_questions) >= 10:
                    logger.warning(f"[Interactive Orchestrator] Session {session.id} reached max 10 questions.")
                    break
                
                session.questions.append(ClarificationQuestion(
                    id=q_id,
                    title=q_data.get("title", ""),
                    question=q_data.get("question", ""),
                    type=q_data.get("type", "text"),
                    category=q_data.get("category"),
                    required=q_data.get("required", True),
                    options=q_data.get("options", []),
                    reason=q_data.get("reason", "")
                ))
                filtered_questions.append(q_data)
        
        audit_log.generated_questions = filtered_questions
        await audit_log.insert()

        if not filtered_questions:
            logger.warning("[Interactive Orchestrator] LLM returned only duplicate/invalid questions. Forcing manual review.")
            session.status = "manual_review_required"
            await session.save()
            return {"status": "manual_review_required", "reason": "AI generated duplicate or invalid questions."}

        session.status = "waiting_for_user"
        tracker = get_current_cost_tracker()
        if tracker:
            session.cost_steps = tracker.to_dict_list()
        await session.save()
        
        return {
            "status": "needs_clarification",
            "sessionId": str(session.id),
            "questions": filtered_questions
        }

    # Otherwise complete (generate_report or manual_review)
    session.accumulated_processing_time_ms = total_processing_time
    session.status = "completed" if next_action == "generate_report" else "manual_review_required"
    await session.save()
    
    await audit_log.insert()
    
    analysis_data = coverage_analysis.get("analysis", coverage_analysis)
    
    final_report = generate_report(
        policy_json=policy_json,
        prescription_json=prescription_json,
        business_rule_results=br_results,
        coverage_analysis=analysis_data,
        processing_time_ms=total_processing_time
    )

    if str(final_report.get("overallStatus", "")).startswith("Invalid"):
        logger.info("[Interactive Orchestrator] Invalid report status; skipping DB insertion as requested.")
        return final_report

    report = AnalysisReport(
        user_id=session.user_id,
        policy_id=session.policy_id,
        prescription_id=session.prescription_id,
        status="completed" if next_action == "generate_report" else "manual_review_required",
        analysis_version="2.1.0",
        policy_text=session.policy_text,
        prescription_text=session.prescription_text,
        policy_json=policy_json,
        prescription_json=prescription_json,
        business_rules=br_results,
        coverage_analysis=analysis_data,
        document_validity=final_report.get("documentValidity"),
        overall_status=final_report.get("overallStatus"),
        dominance_score=final_report.get("dominanceScore"),
        coverage_breakdown=final_report.get("coverageBreakdown"),
        summary=final_report.get("summary"),
        comparison=final_report.get("comparison"),
        processing_time_ms=total_processing_time,
        
        decision_type="Automatic" if next_action == "generate_report" else "Manual Review",
        confidence_score=confidence,
        policy_clauses_used=analysis_data.get("policyClausesUsed", []),
        prescription_evidence=analysis_data.get("prescriptionEvidence", []),
        clarification_answers_used=clarification_history,
        session_id=str(session.id),
        error_message=coverage_analysis.get("reason") if next_action == "manual_review" else None
    )
    await report.insert()
    logger.info(
        f"\n{'='*70}\n"
        f"📊 [ANALYSIS COMPLETED] Report #{report.report_number} | ID: {report.id}\n"
        f"   • Decision:         {report.overall_status} ({report.decision_type})\n"
        f"   • Dominance Score:  {report.dominance_score}%\n"
        f"   • Processing Time:  {total_processing_time}ms ({total_processing_time/1000.0:.2f}s)\n"
        f"   • Session ID:       {session.id}\n"
        f"{'='*70}"
    )

    tracker = get_current_cost_tracker()
    if tracker:
        session.cost_steps = tracker.to_dict_list()
        tracker.report_number = report.report_number
        tracker.print_summary_table(doc_summary=f"End-to-End Pipeline ({report.overall_status})")
    
    session.report_id = str(report.id)
    await session.save()

    if background_tasks:
        background_tasks.add_task(
            _background_post_processing,
            user_id=session.user_id,
            report_id=str(report.id),
            policy_doc_id=session.policy_id if len(session.policy_id or "") > 20 else None,
            policy_file_id=None, 
            prescription_id=session.prescription_id
        )

    fresh = await AnalysisReport.get(report.id)
    result = fresh.dict(by_alias=True)
    result["status"] = "complete" if next_action == "generate_report" else "manual_review_required"
    return result

async def resume_analysis_session(
    session_id: str,
    user_id: str,
    answers: dict,
    background_tasks: Optional[BackgroundTasks] = None
) -> dict:
    logger.info(f"[Interactive Orchestrator] resume_analysis_session called. session_id={session_id}, answers={answers}")
    session = await AnalysisSession.get(ObjectId(session_id))
    if not session or session.user_id != user_id:
        raise ValueError("Session not found or unauthorized")

    if session.status not in ("waiting_for_user", "needs_clarification"):
        logger.warning(f"[Interactive Orchestrator] Session {session_id} has unexpected status '{session.status}'")
        raise ValueError(f"Session is not waiting for user input (current status: {session.status})")

    tracker = AnalysisCostTracker(session_id=str(session.id))
    if session.cost_steps:
        tracker.load_steps(session.cost_steps)
    set_current_cost_tracker(tracker)

    logger.info(f"[Interactive Orchestrator] Storing {len(answers)} answers for session {session_id}")
    for q_id, answer_val in answers.items():
        session.answers.append(ClarificationAnswer(
            questionId=q_id,
            answer=answer_val
        ))
    
    # Check if policy start date conflict was answered
    date_ans = answers.get("policy_start_date_conflict")
    if date_ans:
        import re
        match = re.search(r"(\d{2}[-/]\d{2}[-/]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})", str(date_ans))
        if match:
            chosen_parsed, _ = _parse_and_format_date(match.group(1))
            if chosen_parsed:
                chosen_iso_date = chosen_parsed.strftime("%Y-%m-%d")
                if not session.policy_json:
                    session.policy_json = {}
                session.policy_json["policyStartDate"] = chosen_iso_date
                logger.info(f"[Interactive Orchestrator] Resolved policy start date from user answer: {chosen_iso_date}")
                
                # Persist chosen start date in Policy MongoDB record
                if session.policy_id:
                    try:
                        if len(str(session.policy_id)) == 24:
                            pol_record = await Policy.get(ObjectId(session.policy_id))
                            if pol_record:
                                pol_record.policy_start_date = chosen_parsed
                                if pol_record.extracted_policy_json:
                                    pol_record.extracted_policy_json["policyStartDate"] = chosen_iso_date
                                await pol_record.save()
                                logger.info(f"[Interactive Orchestrator] Saved resolved policy start date {chosen_parsed} to Policy {pol_record.id}")
                    except Exception as pe:
                        logger.error(f"[Interactive Orchestrator] Error updating policy start date in DB: {pe}")

    # Check if treatment date was answered
    treatment_date_ans = answers.get("prescription_treatment_date")
    if treatment_date_ans:
        import re
        match = re.search(r"(\d{2}[-/]\d{2}[-/]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})", str(treatment_date_ans))
        if match:
            chosen_parsed, _ = _parse_and_format_date(match.group(1))
            if chosen_parsed:
                if not session.prescription_json:
                    session.prescription_json = {}
                session.prescription_json["visitDate"] = chosen_parsed.strftime("%Y-%m-%d")
                session.prescription_json["consultationDate"] = chosen_parsed.strftime("%Y-%m-%d")
        elif "today" in str(treatment_date_ans).lower():
            if not session.prescription_json:
                session.prescription_json = {}
            today_str = datetime.now().strftime("%Y-%m-%d")
            session.prescription_json["visitDate"] = today_str
            session.prescription_json["consultationDate"] = today_str

    # Check if hospitalization status was answered
    hosp_ans = str(answers.get("hospitalization_status", "") or answers.get("admission_type", "")).lower()
    if hosp_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        if "yes" in hosp_ans or "admitted" in hosp_ans or "planned" in hosp_ans or "inpatient" in hosp_ans or "daycare" in hosp_ans:
            session.prescription_json["hospitalizationRequired"] = True
        elif "no" in hosp_ans or "opd" in hosp_ans or "clinic" in hosp_ans or "not admitted" in hosp_ans:
            session.prescription_json["hospitalizationRequired"] = False

    # Check if pre-existing status was answered
    ped_ans = str(answers.get("pre_existing_status", "")).lower()
    if ped_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        if "yes" in ped_ans or "pre-existing" in ped_ans:
            session.prescription_json["isPreExisting"] = True
        elif "no" in ped_ans or "first time" in ped_ans or "new" in ped_ans:
            session.prescription_json["isPreExisting"] = False

    # Check if symptom onset / duration / past medical history was answered
    onset_ans = str(answers.get("symptom_onset_duration", "")).lower()
    pmh_ans = str(answers.get("past_medical_history", "")).lower()
    if "pre-existing" in onset_ans or "pre-existing" in pmh_ans or "prior to policy" in onset_ans or "yes, previously" in pmh_ans or "years ago" in onset_ans or "months before" in onset_ans or "chronic" in onset_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        session.prescription_json["isPreExisting"] = True
        import re
        from datetime import timedelta
        match = re.search(r"(\d{2}[-/]\d{2}[-/]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})", str(onset_ans))
        if match:
            parsed_onset, _ = _parse_and_format_date(match.group(1))
            if parsed_onset:
                session.prescription_json["diagnosisDate"] = parsed_onset.strftime("%Y-%m-%d")
        else:
            # Approximate diagnosis date relative to policy start date
            pol_start_dt = None
            if session.policy_json and session.policy_json.get("policyStartDate"):
                pol_start_dt, _ = _parse_and_format_date(session.policy_json.get("policyStartDate"))
            base_date = pol_start_dt or datetime.now()

            if "more than 2 years" in onset_ans or "> 2 years" in onset_ans or "long-standing" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=750)).strftime("%Y-%m-%d")
            elif "1 to 2 years" in onset_ans or "1 year" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=400)).strftime("%Y-%m-%d")
            elif "1 to 6 months" in onset_ans or "recent" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=90)).strftime("%Y-%m-%d")
            else:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=180)).strftime("%Y-%m-%d")
    elif "today" in onset_ans or "first time" in pmh_ans or "new illness" in onset_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        session.prescription_json["isPreExisting"] = False

    policy_json = session.policy_json if hasattr(session, "policy_json") and session.policy_json else {}
    prescription_json = session.prescription_json or {}
    br_results = enforce_business_rules(policy_json, prescription_json)
    session.business_rules = br_results
    session.status = "reanalyzing"
    await session.save()

    logger.info(f"[Interactive Orchestrator] Re-running LLM analysis for session {session_id}")
    return await _run_llm_analysis(session, policy_json, prescription_json, br_results, background_tasks)
