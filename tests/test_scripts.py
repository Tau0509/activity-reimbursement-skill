"""Behavioral tests for the reimbursement scripts using only temporary fixtures."""
import csv
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = load_script('check_ledger')

    @staticmethod
    def item(**overrides):
        item = {
            'id': 'E01', 'name': '测试物料', 'invoice_amount': '100.00',
            'invoices': [{'number': 'INV-1', 'source': '发票.pdf'}],
            'orders': ['订单.jpg'],
            'payments': [{'amount': '120.00', 'transaction_id': 'T-1', 'source': '付款.jpg'}],
            'refunds': [{'amount': '20.00', 'transaction_id': 'R-1', 'source': '退款.jpg', 'verified': True}],
        }
        item.update(overrides)
        return item

    def test_verified_partial_refund_and_totals(self):
        result = self.ledger.reconcile({
            'items': [self.item()], 'expected_invoice_total': '100.00',
            'expected_net_payment_total': '100.00',
        })
        row = result['items'][0]
        self.assertEqual((row['gross_payment'], row['verified_refund'], row['net_payment'], row['difference']),
                         ('120.00', '20.00', '100.00', '0.00'))
        self.assertEqual(result['totals']['net_payment'], '100.00')
        self.assertTrue(result['arithmetic_complete'])
        self.assertEqual(result['issues'], [])

    def test_unverified_refund_is_not_deducted(self):
        result = self.ledger.reconcile({'items': [self.item(refunds=[
            {'amount': '20.00', 'source': '退款.jpg', 'verified': False}
        ])]})
        self.assertEqual(result['items'][0]['net_payment'], '120.00')
        self.assertEqual(result['items'][0]['difference'], '-20.00')
        self.assertIn('unverified_refund_not_deducted', {i['type'] for i in result['issues']})

    def test_missing_payment_is_unknown_not_zero(self):
        for payments in (None, []):
            with self.subTest(payments=payments):
                result = self.ledger.reconcile({'items': [self.item(payments=payments, refunds=[])]})
                self.assertIsNone(result['items'][0]['net_payment'])
                self.assertIsNone(result['totals']['net_payment'])
                self.assertIsNone(result['items'][0]['difference'])
                self.assertFalse(result['arithmetic_complete'])
                self.assertIn('missing_payment_records', {i['type'] for i in result['issues']})

    def test_duplicate_invoice_and_transaction_are_reported(self):
        first = self.item()
        second = self.item(id='E02', invoices=[{'number': 'INV-1', 'source': '另发票.pdf'}],
                           payments=[{'amount': '100', 'transaction_id': 'T-1', 'source': '另付款.jpg'}],
                           refunds=[])
        result = self.ledger.reconcile({'items': [first, second]})
        issues = {i['type']: i for i in result['issues']}
        self.assertEqual(issues['duplicate_invoice_number']['locations'], ['E01', 'E02'])
        self.assertEqual(issues['duplicate_transaction_id']['locations'], ['E01:payment', 'E02:payment'])

    def test_duplicate_item_id_and_excess_refund_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.reconcile({'items': [self.item(), self.item()]})
        with self.assertRaises(ValueError):
            self.ledger.reconcile({'items': [self.item(refunds=[
                {'amount': '121.00', 'source': '退款.jpg', 'verified': True}
            ])]})

    def test_invalid_amounts_and_cli_exit_status(self):
        for value in ('-1', '1.001', 'NaN', 'Infinity', 'abc', True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ledger.reconcile({'items': [self.item(invoice_amount=value)]})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ledger.json'
            path.write_text(json.dumps({'items': [self.item(invoice_amount='-1')]}), encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_ledger.py'), str(path)],
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error', json.loads(result.stdout))


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packager = load_script('package_packet')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.first = self.root / '原件一.txt'
        self.second = self.root / '原件二.txt'
        self.first.write_bytes('虚拟中文凭证一\n'.encode('utf-8'))
        self.second.write_bytes('虚拟中文凭证二\n'.encode('utf-8'))
        self.manifest = self.root / 'manifest.json'
        self.output = self.root / 'output'
        self.data = {
            'name': '示例活动报销包', 'instructions': '仅供测试。',
            'files': [
                {'source': str(self.first), 'path': '01_成果/汇编.txt', 'category': '成果'},
                {'source': str(self.second), 'path': '02_凭证/F01.txt', 'category': '凭证'},
            ],
        }

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def test_dry_run_and_package_integrity(self):
        self.write_manifest()
        before = {p: p.read_bytes() for p in (self.first, self.second)}
        dry = self.packager.package(self.manifest, self.output, dry_run=True)
        self.assertTrue(dry['dry_run'])
        self.assertFalse(self.output.exists())
        result = self.packager.package(self.manifest, self.output)
        self.assertEqual((result['source_files'], result['total_files']), (2, 4))
        archive = Path(result['archive'])
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result['sha256'])
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip())
            members = z.namelist()
            self.assertEqual(len(members), 4)
            self.assertTrue(all(name.startswith('示例活动报销包/') for name in members))
            rows = list(csv.DictReader(io.StringIO(z.read(
                '示例活动报销包/00_文件清单与命名对照.csv').decode('utf-8-sig'))))
            self.assertEqual(len(rows), 2)
            by_path = {row['整理后路径']: row for row in rows}
            for entry in self.data['files']:
                original = Path(entry['source'])
                contents = z.read('示例活动报销包/' + entry['path'])
                self.assertEqual(contents, before[original])
                self.assertEqual(by_path[entry['path']]['SHA256'], hashlib.sha256(contents).hexdigest())
        for original, data in before.items():
            self.assertEqual(original.read_bytes(), data)
        with self.assertRaises(ValueError):
            self.packager.package(self.manifest, self.output)

    def test_collision_and_unsafe_paths_are_rejected(self):
        cases = [
            ('相同名称', '目录/文件.txt', '目录/文件.txt'),
            ('大小写冲突', '目录/F01.txt', '目录/f01.txt'),
            ('文件目录冲突', '目录', '目录/文件.txt'),
            ('保留名称冲突', '正常.txt', '00_阅读说明.txt'),
            ('父目录穿越', '正常.txt', '../越界.txt'),
            ('中途穿越', '正常.txt', '目录/../越界.txt'),
            ('绝对路径', '正常.txt', '/tmp/越界.txt'),
            ('反斜线', '正常.txt', '目录\\文件.txt'),
        ]
        for label, first_path, second_path in cases:
            with self.subTest(case=label):
                self.data['files'][0]['path'] = first_path
                self.data['files'][1]['path'] = second_path
                self.write_manifest()
                with self.assertRaises(ValueError):
                    self.packager.package(self.manifest, self.output, dry_run=True)
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
