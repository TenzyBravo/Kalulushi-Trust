from django.db import migrations

# Database rules the ORM cannot declare (slice 2 design §4 and §5.5).
SQL = """
-- A register belongs to a class of the register's academic year.
ALTER TABLE attendance_sessions ADD CONSTRAINT attendance_sessions_class_in_year
    FOREIGN KEY (class_id, academic_year_id) REFERENCES classes (id, academic_year_id);

-- A record sits in a register of its own class ...
ALTER TABLE attendance_records ADD CONSTRAINT attendance_records_session_class
    FOREIGN KEY (session_id, class_id) REFERENCES attendance_sessions (id, class_id);

-- ... and uses that learner's enrollment in that same class.
ALTER TABLE attendance_records ADD CONSTRAINT attendance_records_enrollment_matches
    FOREIGN KEY (enrollment_id, class_id, learner_id) REFERENCES enrollments (id, class_id, learner_id);

-- Follow-up history is never overwritten (baseline §6).
CREATE OR REPLACE FUNCTION follow_up_actions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'follow_up_actions is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER follow_up_actions_no_update_or_delete
    BEFORE UPDATE OR DELETE ON follow_up_actions
    FOR EACH ROW EXECUTE FUNCTION follow_up_actions_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS follow_up_actions_no_update_or_delete ON follow_up_actions;
DROP FUNCTION IF EXISTS follow_up_actions_append_only();
ALTER TABLE attendance_records DROP CONSTRAINT IF EXISTS attendance_records_enrollment_matches;
ALTER TABLE attendance_records DROP CONSTRAINT IF EXISTS attendance_records_session_class;
ALTER TABLE attendance_sessions DROP CONSTRAINT IF EXISTS attendance_sessions_class_in_year;
"""


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
