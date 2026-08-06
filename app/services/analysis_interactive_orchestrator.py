from typing import Optional
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
from app.services.analysis_orchestrator import _fetch_file_bytes_by_gridfs_id, _background_post_processing

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

    try:
        async def process_document(doc_id):
            bytes_data, mime = await _fetch_file_bytes_by_gridfs_id(doc_id, user_id)
            raw_text = await extract_text_from_document(bytes_data, mime)
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
            if rx_doc.is_manual or rx_doc.manual_text:
                is_manual_rx = True
                manual_rx_text = rx_doc.manual_text or rx_doc.extracted_prescription_text or ""
            elif rx_doc.grid_fs_file_id:
                target_rx_file_id = rx_doc.grid_fs_file_id

        async def load_rx_text():
            if is_manual_rx and manual_rx_text:
                return clean_text(manual_rx_text)
            try:
                return await process_document(target_rx_file_id)
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
                policy_text = policy_doc.extracted_policy_text
                existing_policy_json = policy_doc.extracted_policy_json or {}
                rx_text = await load_rx_text()
            else:
                policy_text, rx_text = await asyncio.gather(
                    process_document(policy_doc.grid_fs_file_id),
                    load_rx_text()
                )
        else:
            policy_text, rx_text = await asyncio.gather(
                process_document(policy_file_id),
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
            prescription_json["isManual"] = True
            prescription_json["prescriptionSource"] = "Self-entered Prescription"
            prescription_json["manualText"] = rx_text
            if rx_doc:
                rx_doc.extracted_prescription_text = rx_text
                rx_doc.extracted_prescription_json = prescription_json
                await rx_doc.save()
        
        session.policy_json = policy_json
        session.prescription_json = prescription_json

        # Early check for document validity
        is_policy_valid = validation.get("isPolicyValid", True)
        is_rx_valid = validation.get("isPrescriptionValid", True)
        
        if not is_policy_valid or not is_rx_valid:
            logger.warning(f"[Interactive Orchestrator] Document validation failed: policyValid={is_policy_valid}, prescriptionValid={is_rx_valid}")
            if not is_policy_valid and not is_rx_valid:
                inv_status = "Invalid Policy and Prescription"
            elif not is_policy_valid:
                inv_status = "Invalid Policy"
            else:
                inv_status = "Invalid Prescription"

            extraction_time_ms = int((time.time() - start_time) * 1000)
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
                        "isPrescriptionValid": is_rx_valid
                    },
                    "overallStatus": inv_status,
                    "comparison": []
                },
                processing_time_ms=extraction_time_ms
            )

            report = AnalysisReport(
                user_id=session.user_id,
                policy_id=session.policy_id,
                prescription_id=session.prescription_id,
                status="completed",
                analysis_version="2.1.0",
                policy_text=session.policy_text,
                prescription_text=session.prescription_text,
                policy_json=policy_json,
                prescription_json=prescription_json,
                business_rules={},
                coverage_analysis={},
                document_validity=final_report.get("documentValidity"),
                overall_status=final_report.get("overallStatus"),
                dominance_score=0.0,
                coverage_breakdown=final_report.get("coverageBreakdown"),
                summary=final_report.get("summary"),
                summary_text=final_report.get("summaryText"),
                comparison=[],
                processing_time_ms=extraction_time_ms,
                decision_type="Automatic",
                confidence_score=0,
                policy_clauses_used=[],
                prescription_evidence=[],
                clarification_answers_used=[],
                session_id=str(session.id),
                error_message=None
            )
            await report.insert()
            session.report_id = str(report.id)
            await session.save()

            fresh = await AnalysisReport.get(report.id)
            result = fresh.dict(by_alias=True)
            result["status"] = "complete"
            return result
        
        br_results = enforce_business_rules(policy_json, prescription_json)
        session.business_rules = br_results

        # Track extraction & business rule processing time
        extraction_time_ms = int((time.time() - start_time) * 1000)
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
    total_processing_time = (session.accumulated_processing_time_ms or 0) + llm_round_duration_ms
    session.accumulated_processing_time_ms = total_processing_time

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
        await session.save()
        
        return {
            "status": "needs_clarification",
            "sessionId": str(session.id),
            "questions": filtered_questions
        }

    # Otherwise complete (generate_report or manual_review)
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
        summary_text=final_report.get("summaryText"),
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
    logger.info(f"[Interactive Orchestrator] Completed report {report.id} in total processing time: {total_processing_time}ms")
    
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

    logger.info(f"[Interactive Orchestrator] Storing {len(answers)} answers for session {session_id}")
    for q_id, answer_val in answers.items():
        session.answers.append(ClarificationAnswer(
            questionId=q_id,
            answer=answer_val
        ))
    
    session.status = "reanalyzing"
    await session.save()
    
    policy_json = session.policy_json if hasattr(session, "policy_json") and session.policy_json else {}
    prescription_json = session.prescription_json or {}
    br_results = session.business_rules or {}

    logger.info(f"[Interactive Orchestrator] Re-running LLM analysis for session {session_id}")
    return await _run_llm_analysis(session, policy_json, prescription_json, br_results, background_tasks)
