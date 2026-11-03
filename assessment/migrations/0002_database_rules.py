from django.db import migrations

# Rules the ORM cannot declare (slice 3 design §3, §5, §7).
SQL = """
-- A mark belongs to its assessment's class subject, that class subject's class,
-- and the learner's enrollment in that class.
ALTER TABLE assessment_results ADD CONSTRAINT assessment_results_assessment_class_subject
    FOREIGN KEY (assessment_id, class_subject_id) REFERENCES assessments (id, class_subject_id);
ALTER TABLE assessment_results ADD CONSTRAINT assessment_results_class_subject_class
    FOREIGN KEY (class_subject_id, class_id) REFERENCES class_subjects (id, class_id);
ALTER TABLE assessment_results ADD CONSTRAINT assessment_results_enrollment_matches
    FOREIGN KEY (enrollment_id, class_id, learner_id) REFERENCES enrollments (id, class_id, learner_id);

-- Competency results sit on their class's sheet and use the learner's enrollment in that class.
ALTER TABLE learner_competency_results ADD CONSTRAINT competency_results_sheet_class
    FOREIGN KEY (sheet_id, class_id) REFERENCES competency_sheets (id, class_id);
ALTER TABLE learner_competency_results ADD CONSTRAINT competency_results_enrollment_matches
    FOREIGN KEY (enrollment_id, class_id, learner_id) REFERENCES enrollments (id, class_id, learner_id);

-- A score can never exceed the assessment's maximum.
CREATE OR REPLACE FUNCTION assessment_results_score_within_max() RETURNS trigger AS $$
DECLARE max_allowed numeric;
BEGIN
    IF NEW.score IS NOT NULL THEN
        SELECT max_score INTO max_allowed FROM assessments WHERE id = NEW.assessment_id;
        IF NEW.score > max_allowed THEN
            RAISE EXCEPTION 'score % is above the maximum of %', NEW.score, max_allowed USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER assessment_results_score_within_max
    BEFORE INSERT OR UPDATE ON assessment_results
    FOR EACH ROW EXECUTE FUNCTION assessment_results_score_within_max();

-- Result history is never overwritten.
CREATE OR REPLACE FUNCTION assessment_result_history_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'assessment_result_history is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER assessment_result_history_no_update_or_delete
    BEFORE UPDATE OR DELETE ON assessment_result_history
    FOR EACH ROW EXECUTE FUNCTION assessment_result_history_append_only();

-- The rules behind recorded results never change: bands of a non-draft scheme,
-- and competencies or levels of a non-draft framework, are frozen.
CREATE OR REPLACE FUNCTION grade_bands_frozen() RETURNS trigger AS $$
DECLARE scheme_status text;
BEGIN
    SELECT status INTO scheme_status FROM grading_schemes WHERE id = COALESCE(NEW.scheme_id, OLD.scheme_id);
    IF scheme_status <> 'draft' THEN
        RAISE EXCEPTION 'grade bands of an active or retired scheme cannot change; make a new version';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER grade_bands_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON grade_bands
    FOR EACH ROW EXECUTE FUNCTION grade_bands_frozen();

CREATE OR REPLACE FUNCTION competency_framework_frozen() RETURNS trigger AS $$
DECLARE framework_status text;
BEGIN
    SELECT status INTO framework_status FROM competency_frameworks WHERE id = COALESCE(NEW.framework_id, OLD.framework_id);
    IF framework_status <> 'draft' THEN
        RAISE EXCEPTION 'competencies and levels of an active or retired framework cannot change; make a new version';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER competencies_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON competencies
    FOR EACH ROW EXECUTE FUNCTION competency_framework_frozen();
CREATE TRIGGER competency_levels_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON competency_levels
    FOR EACH ROW EXECUTE FUNCTION competency_framework_frozen();
"""

REVERSE = """
DROP TRIGGER IF EXISTS competency_levels_frozen ON competency_levels;
DROP TRIGGER IF EXISTS competencies_frozen ON competencies;
DROP FUNCTION IF EXISTS competency_framework_frozen();
DROP TRIGGER IF EXISTS grade_bands_frozen ON grade_bands;
DROP FUNCTION IF EXISTS grade_bands_frozen();
DROP TRIGGER IF EXISTS assessment_result_history_no_update_or_delete ON assessment_result_history;
DROP FUNCTION IF EXISTS assessment_result_history_append_only();
DROP TRIGGER IF EXISTS assessment_results_score_within_max ON assessment_results;
DROP FUNCTION IF EXISTS assessment_results_score_within_max();
ALTER TABLE learner_competency_results DROP CONSTRAINT IF EXISTS competency_results_enrollment_matches;
ALTER TABLE learner_competency_results DROP CONSTRAINT IF EXISTS competency_results_sheet_class;
ALTER TABLE assessment_results DROP CONSTRAINT IF EXISTS assessment_results_enrollment_matches;
ALTER TABLE assessment_results DROP CONSTRAINT IF EXISTS assessment_results_class_subject_class;
ALTER TABLE assessment_results DROP CONSTRAINT IF EXISTS assessment_results_assessment_class_subject;
"""


class Migration(migrations.Migration):
    dependencies = [("assessment", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
