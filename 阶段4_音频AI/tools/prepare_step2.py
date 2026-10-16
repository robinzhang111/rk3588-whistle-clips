"""Freeze source-use policy and protocol metadata; no inference or relabelling."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "阶段4_音频AI"
OLD = ROOT / "阶段2_离线哨声检测"
STEP1 = STAGE / "outputs/01_data_literature_audit_20261016_02"
spec = importlib.util.spec_from_file_location("source_audit", STAGE / "tools/audit_step1.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sha = audit.sha


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def validate_families(families, assets):
    ids = {f["family_id"] for f in families}
    if len(ids) != len(families) or any(set(a["family_ids"])-ids for a in assets):
        raise ValueError("Duplicate/missing source family")
    for block in {f["leakage_block"] for f in families}:
        if len({f["active_assignment"] for f in families if f["leakage_block"] == block}) != 1:
            raise ValueError("Leakage block crosses active assignments")
    blocks = {f["family_id"]: f["leakage_block"] for f in families}
    identical_media = {}
    for row in assets:
        if row.get("role") in ("original_media", "historical_decoded_audio"):
            identical_media.setdefault(row["sha256"], set()).update(blocks[f] for f in row["family_ids"])
    if any(len(group) > 1 for group in identical_media.values()):
        raise ValueError("Identical media crosses leakage blocks")


def build(out):
    if STAGE not in out.resolve().parents or out.exists():
        raise ValueError("Use a new output directory inside stage4")
    out.mkdir(parents=True)
    manifest = read(STEP1 / "manifest.json")
    media = read(STEP1 / "media.json")
    protected, assets, families = {}, [], []

    def asset(path, role, family_ids=(), expected=None):
        path = path.resolve()
        digest = sha(path)
        if expected and expected != digest:
            raise ValueError("Source changed: " + str(path))
        protected[str(path)] = digest
        row = dict(path=path.relative_to(ROOT).as_posix(), sha256=digest,
                   bytes=path.stat().st_size, role=role, family_ids=list(family_ids))
        if row["path"] in media:
            row["streams"] = [{k:s.get(k) for k in ("codec_type", "codec_name", "channels", "sample_rate", "duration")}
                              for s in media[row["path"]]["probe"]["streams"]]
            row["media_check_scope"] = media[row["path"]]["full_audio_decode"]
        assets.append(row)
        return row

    def family(fid, domain, pool, block, **extra):
        row = dict(family_id=fid, domain=domain, primary_pool=pool, leakage_block=block,
                   active_assignment="historical_regression_only" if pool == "historical_auxiliary_regression" else "development_only",
                   independent_holdout_qualified=False, channel_policy="all channels/derivatives stay in family",
                   media_rights="not_independently_verified; existing user use authorization retained",
                   pretrained_overlap="unknown", **extra)
        row["data_origin"] = {"bhuman":"https://b-human.informatik.uni-bremen.de/public/datasets/whistle-2017/",
            "robocup":"https://github.com/NaoDevils/AudioProcessing; linked detection-data DOI; remote bytes not verified",
            "volleyball":"https://huggingface.co/datasets/GYdevy/volleyball-whistles; remote bytes not verified",
            "basketball":"local user media; original URL/teams/date unknown"}[domain]
        row["document_license_declaration"] = "MIT declaration; media rights tracked separately" if domain in ("robocup", "volleyball") else "explicit media license not independently established"
        families.append(row)
        return row

    bh_manifest = read(OLD / "data/bhuman2017-prepared-v1/manifest.json")
    for group in sorted({r["group_id"] for r in bh_manifest}):
        rows = [r for r in bh_manifest if r["group_id"] == group]
        family("bhuman:"+group, "bhuman", "historical_auxiliary_regression", "bhuman:"+group,
               original_recording_or_match=group, relationship_basis="existing frozen source associations",
               historical_split=rows[0]["split_proposal"], development_exposure="historical evaluations/diagnostics",
               original_label_granularity="publisher_interval; target_field_scope", new_task_negative_coverage=False,
               training_candidate=False)
    family("naodevils:unmapped", "robocup", "historical_auxiliary_regression", "naodevils:unmapped",
           original_recording_or_match=None, relationship_basis="unknown row-to-recording map; all kept in one block",
           development_exposure="upstream validation/threshold selection design and local re-evaluation",
           original_label_granularity="NPY window labels; raw TestBench has separate 68-blow historical reference",
           new_task_negative_coverage=False, training_candidate=False)
    for i in range(1, 15):
        match = "match"+str(i)
        has_labels = i not in (5, 6, 10, 12)
        family("volleyball:"+match, "volleyball", "training_candidate" if has_labels else "deferred",
               "volleyball:origin-unresolved", original_recording_or_match=match,
               relationship_basis="recording stem only; cross-match relation unknown; one conservative leakage block",
               development_exposure="step1 labels/signal inspected; prior model use unknown",
               original_label_granularity="manual_click/algorithm_anchor" if has_labels else "none",
               training_candidate=has_labels, training_ready=False, new_task_negative_coverage=False)
    proposal = read(OLD / "outputs/combined-data-split-plan-20261012-01/basketball-source-split-proposal.json")
    by_video = {}
    for row in proposal["assignments"]:
        fid = row["id"]
        by_video[row["video_filename"]] = fid
        family("basketball:"+fid, "basketball", "deferred" if fid == "B01" else "development_validation_candidate",
               "basketball:origin-unresolved", original_recording_or_match=row["group_id"],
               relationship_basis=row["source_basis"]+"; no cross-source independence proof",
               development_exposure="confirmed model feedback/selection exposure" if fid.startswith("B") else "historical human review; later model exposure not established",
               old_split_proposal=row["proposed_split"], old_split_adopted=False,
               training_candidate=fid != "B01", training_ready=False,
               original_label_granularity="human_approximate_single_blow",
               historical_reference_events=row["event_count"], new_task_negative_coverage=False,
               pending=["physical_whistle_vs_mouth_whistle_semantics", "natural_bounds_and_coverage_under_new_task"]+
                       (["B01 legacy 1-blow versus later confirmed 2-blow version reconciliation"] if fid == "B01" else []))
        derived = asset(Path(row["audio_path"]), "historical_decoded_audio", ["basketball:"+fid], row["audio_sha256"])
        derived.update(channels=row["channels"], rate=row["rate"], frames=row["frames"],
                       derived_from="data/video/"+row["video_filename"], relationship="decoded source audio; all channels kept; no new crop")
    for row in manifest:
        path = ROOT / row["path"]
        parts = path.relative_to(ROOT / "data").parts
        if parts[0] == "whistle":
            ids = ["bhuman:"+audit.bh.GROUPS[path.name][0]] if path.suffix == ".wav" else [f["family_id"] for f in families if f["domain"] == "bhuman"]
        elif parts[0] == "Robcup":
            ids = ["naodevils:unmapped"]
        elif parts[0] == "video":
            ids = ["basketball:"+by_video[path.name]]
        else:
            ids = ["volleyball:"+path.stem] if path.suffix == ".wav" else ["volleyball:match"+str(i) for i in range(1, 15)]
        asset(path, "original_media" if path.suffix.lower() in (".wav", ".mp4") else "original_array_or_metadata", ids, row["sha256"])
    asset(ROOT / "测试.mp4", "related_unenrolled_media; no inference", ["basketball:"+r["id"] for r in proposal["assignments"]])
    refs = [OLD / "outputs/basketball-manual-review-20261012-04/manual_annotations_corrected.json",
            OLD / "outputs/basketball-new-manual-review-20261012-02/manual_annotations_coverage_confirmed.json",
            OLD / "outputs/testbench-manual-review-20261011-04/manual_annotations_corrected.json",
            OLD / "outputs/pc-correction-20261012-01/user-intake-20261013-01/video-corrections-chat-confirmed.json",
            OLD / "outputs/pc-correction-20261012-01/acceptance-20261013.json",
            OLD / "evaluation/protocol-v2.json", OLD / "evaluation/splits-v1.json",
            OLD / "outputs/naodevils-review-20261011-01/LICENSE",
            OLD / "outputs/naodevils-review-20261011-01/ReadMe.md",
            OLD / "outputs/basketball-new-manual-20261012-01/review.html",
            OLD / "outputs/basketball-manual-20261012-01/review.html",
            OLD / "outputs/basketball-new-manual-review-20261012-02/material-scoped.json"]
    refs += [OLD / "tools" / name for name in ("detect_file.py", "continuous_whistle.py", "basketball_audio_adapter.py",
             "evaluate_testbench_events.py", "validate_basketball_annotations.py", "whistle_predictor.py")]
    refs += list(STEP1.glob("*"))
    for path in refs:
        asset(path, "evidence_or_reused_tool")
    models = [(OLD / "outputs/naodevils-review-20261011-01/PreTrainedNetworks/WhistleDetection/WhistleNetMk16.h5",
               "research_tensorflow_cpu", "924e779a5791eba1ba26def09a368dae904323e73a2ae240061a844c09f96f75"),
              (OLD / "outputs/rk3588-file-20261013-01/export-onnx/WhistleNetMk16-float32.onnx",
               "alternate_reference_not_selected", "4fcf70a935cdca1a7618480ca9df5375a476bddacf35a8fe03db13617441204f"),
              (OLD / "outputs/rk3588-file-20261013-01/board-npu/conversion-onnx-fp16/WhistleNetMk16-fp16.rknn",
               "engineering_fp16_not_executed", "82f165c8434508d026bb3c51e7528f16463b9eb3a9be7e0d8c4bcbb873395130")]
    model_assets = [asset(path, role, expected=digest) for path, role, digest in models]
    py = OLD / ".venv-naodevils-infer/Scripts/python.exe"
    probe = "import sys,importlib.metadata as m,importlib.util,json; print(json.dumps(dict(executable=sys.executable,python=sys.version,distributions={p:m.version(p) for p in ['tensorflow-intel','numpy','h5py','keras']},module_presence={p:importlib.util.find_spec(p) is not None for p in ['tensorflow','numpy','h5py']})))"
    env = subprocess.run([str(py), "-B", "-c", probe], capture_output=True, check=True)
    environment = json.loads(env.stdout)
    environment.update(probe_scope="metadata/find_spec only; no TensorFlow import, model load or inference")
    validate_families(families, assets)
    assert not any(f["independent_holdout_qualified"] for f in families)
    write(out / "source-families.json", dict(schema="stage4-source-use-v2", status="frozen_policy_not_training_release",
        families=families, assets=assets, pools={pool:[f["family_id"] for f in families if f["primary_pool"] == pool]
        for pool in ("training_candidate", "development_validation_candidate", "historical_auxiliary_regression", "independent_reserved_candidate", "deferred")},
        candidate_training_overlay=[f["family_id"] for f in families if f["training_candidate"]],
        no_independent_basketball_holdout=True, unknown_lineage_policy="no independent train/validation/test split inside unresolved leakage block"))
    protocol = dict(schema="stage4-physical-whistle-single-blow-v1", status="frozen_definition_pending_label_release",
        target="audible physical whistle; source identity irrelevant", labels=["whistle", "non_whistle", "uncertain"],
        mouth_whistle="confirmed mouth whistle: non_whistle with mouth_whistle subtype; uncertain if physical/mouth distinction unreliable; no automatic legacy relabel",
        unit="native recording + uninterrupted segment; cross-channel union once",
        sample_interval="[start,end)", time_basis="relative_only",
        tracks=dict(natural_onset=dict(tolerance_seconds=0.2, basis="existing project candidate retained before new scores; scoring convention not measured label precision",
                    tolerance_samples="floor(rate*0.2); inclusive", matcher="existing evaluate_testbench_events.match chronological DP per scoring island",
                    objectives=["maximum matched cardinality", "minimum absolute onset error sum", "deterministic chronological pair tuples of original indices"],
                    status="protocol fixed; human release needed per recording"),
                    publisher_interval=dict(role="legacy regression only; not natural-single-blow score"),
                    point=dict(status="not_released", tolerance_seconds=None, reason="need point semantics/uncertainty audit first; never inherit 0.2 automatically"),
                    window=dict(role="NPY published classification regression only; no event FP/hour")),
        masks=dict(coverage="explicit reviewed ranges; complement excluded", guard_seconds=0.2,
                   closure="expand base exclusions and clipped events by tolerance; extend whole intersecting GT event plus same guard to fixed point, without predictions",
                   label_eligibility="full natural interval inside one effective island",
                   prediction_eligibility="onset in island; crossing-offset retained for TP/FP but offset error excluded",
                   frozen_before_scoring=True),
        duplicates=dict(definition="unmatched prediction onset inside a matched target interval extended left by onset tolerance, OR within onset tolerance of its start",
                        rate="duplicate_FP / all scored predictions", zero_denominator=None,
                        scope="operational duplicate flag, not proof of physical cause; all unmatched predictions remain FP"),
        fp_hour=dict(eligible="complete-reviewed continuous single-stream duration, all non-whistle background included, not whistle-selected clip", selected_clip_result=None),
        zero_denominators="null/NA with reason; F1=2TP/(2TP+FP+FN) if denominator>0", aggregation="per recording/family plus within-track micro totals; no cross-granularity pooling",
        acceptance_thresholds=None, production_model_or_rules_changed=False, model_execution=False)
    write(out / "protocol.json", protocol)
    write(out / "baseline-plan.json", dict(status="plan_only_not_executed", models=model_assets, environment=environment,
        selected_model_role="research_tensorflow_cpu", threshold=0.65, rule="v1",
        first_inputs=["data/video/3.mp4", "data/video/5.mp4"], first_ids=["N04", "N06"],
        scoring_gate="new-task physical/mouth/uncertain and boundary/coverage release; no identity gate",
        first_metrics=["pipeline integrity", "conditional development TP/FP/FN/P/R/F1/duplicates after release"],
        first_FP_hour=None, minimum_run="N04 only; decide N06 afterwards; no full batch", outputs="stage4/outputs/03_baseline_smoke_<new_id>/N04"))
    scoped = read(OLD / "outputs/basketball-new-manual-review-20261012-02/material-scoped.json")
    truth = read(OLD / "outputs/basketball-new-manual-review-20261012-02/manual_annotations_coverage_confirmed.json")
    pending = []
    playback_checks = []
    for fid in ("N04", "N06"):
        f = next(x for x in scoped["files"] if x["id"] == fid)
        original_page = OLD / "outputs/basketball-new-manual-20261012-01/review.html"
        original_mat = read(original_page.parent / "material.json")
        playback = next(x for x in original_mat["files"] if x["id"] == fid)["audio"]
        for rel in playback:
            playback_checks.append(asset(original_page.parent / rel, "existing_listening_only_not_model_input", ["basketball:"+fid]))
        pending.append(dict(file_id=fid, input_sha256=f["video_sha256"], rate=f["rate"], frames=f["frames"],
            prior_event_ids=[e["id"] for e in truth["events"] if e["file_id"] == fid],
            review_status="pending_user_listening", physical_whistle_semantics_confirmed=None,
            reviewed_ranges=None, uncertain_ranges=None, decisions=[], negative_regions=[],
            mouth_whistle_regions=[], fp_hour_eligible=False,
            note="No confirmation generated; keep old events in old file, new decisions reference IDs"))
    write(out / "pending-review.json", dict(schema="stage4-review-release-v1", tasks=pending,
           all_user_listening_this_round=False, old_UI_supports=["whistle", "uncertain"],
           sidecar_required=["non_whistle", "mouth_whistle subtype", "boundary uncertainty", "reviewed/excluded ranges"],
           playback_files=playback_checks))
    # Listening references are part of the same manifest, after existence checks.
    source_table = read(out / "source-families.json")
    source_table["assets"] = assets
    write(out / "source-families.json", source_table)
    assert all(sha(Path(p)) == h for p, h in protected.items())
    write(out / "integrity.json", dict(protected_sha256=protected, all_unchanged=True,
        root_data_count=len(manifest), root_data_matches_step1=True, family_count=len(families),
        assets_count=len(assets), leakage_conflicts=0, no_model_import_or_execution=True))
    print(json.dumps(dict(output=str(out), families=len(families), assets=len(assets),
                         pools=Counter(f["primary_pool"] for f in families)), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.resolve())
