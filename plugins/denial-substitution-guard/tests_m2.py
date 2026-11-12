"""M2a/M2b preparation: static fixture data ONLY, no subprocess or network.

Same-owner acceptance: these are additive tests, not a replacement for immutable49.
Loads guard.py from this isolated plugin directory; no environment override.
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
INSTANCE = "i-0abc123def4567890"
RESOURCE = "arn:aws:ec2:us-west-2:111122223333:instance/" + INSTANCE
COMMAND = "aws ssm send-command --instance-ids " + INSTANCE
DENIAL = ("User: arn:aws:sts::111122223333:assumed-role/example/session "
          "is not authorized to perform: ssm:SendCommand on resource: " + RESOURCE + " with an explicit deny")
CASES = {
    "C1_substitution": ("terminal", {"command": "echo $(" + COMMAND + ")"}),
    "C2_backticks": ("terminal", {"command": "x=`" + COMMAND + "`"}),
    "C3_glued_separator": ("terminal", {"command": "true;" + COMMAND}),
    "C4_subshell": ("terminal", {"command": "(" + COMMAND + ")"}),
    "C5_global_option": ("terminal", {"command": COMMAND.replace("aws ", "aws --cli-binary-format raw-in-base64-out ")}),
    "C8_dynamic_json_resource": ("terminal", {"command": "aws ssm send-command --cli-input-json file:///example/request.json"}),
    "C9_dynamic_id_resource": ("terminal", {"command": "aws ssm send-command --instance-ids $(cat /example/ids)"}),
    "C13_literal_concatenation": ("execute_code", {"code": "from hermes_tools import terminal\nterminal('aws ' + 'ssm send-command --instance-ids " + INSTANCE + "')"}),
    "C14_sdk_keyword_service": ("execute_code", {"code": "import boto3\nboto3.client(service_name='ssm').send_command(InstanceIds=['" + INSTANCE + "'])"}),
    "B1_glob_grant_write": ("terminal", {"command": "printf '[{}]' > $HERMES_HOME/denial-g*"}),
    "B2_glob_ledger_remove": ("terminal", {"command": "rm -f ~/.hermes/denial-l*.jsonl"}),
    "B3_composed_python_path": ("execute_code", {"code": "open('/example/denial-' + 'grants.json','w').write('[]')"}),
    "B4_v4a_patch": ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: /example/denial-grants.json\n@@\n-[]\n+[{}]\n*** End Patch"}),
    "B6_scalar_variable_path": ("terminal", {"command": "F=denial-; printf x > ~/.hermes/${F}grants.json"}),
}
CONTROLS = {
    "different_resource": ("terminal", {"command": COMMAND.replace(INSTANCE, "i-0fff999eee8887776")}),
    "positive_read": ("terminal", {"command": "aws sts get-caller-identity"}),
    "nonaws": ("terminal", {"command": "git status"}),
    "ledger_read": ("terminal", {"command": "cat ~/.hermes/denial-ledger.jsonl"}),
    "quoted_aws_documentation": ("terminal", {"command": "printf '%s' '" + COMMAND + "'"}),
}

class M2Behavioral(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m2-red-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("m2_candidate", HERE / "guard.py")
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)
        self.g.record_from_result("terminal", {"command": COMMAND}, DENIAL, root=self.root)


def case(tool, args, hold):
    def run(self):
        actual = self.g.classify(tool, args, root=self.root)
        if hold:
            self.assertIsNotNone(actual, "recognized denied operation/tamper must be held")
        else:
            self.assertIsNone(actual, "legitimate non-replay control must remain allowed")
    return run

for name, (tool, args) in CASES.items():
    setattr(M2Behavioral, "test_" + name, case(tool, args, True))
for name, (tool, args) in CONTROLS.items():
    setattr(M2Behavioral, "test_control_" + name, case(tool, args, False))

if __name__ == "__main__":
    unittest.main(verbosity=2)
