# 核账与归档数据

## 台账与索引

一项费用可以关联多笔付款、退款及多个源文件。使用稳定费用编号和来源路径；发票、订单、支付分别编号，保持多对多关系，不强行要求三者张数相同。

建议台账字段：费用编号、费用项目、物品数量及单位、发票金额和号码、订单号、原付款、退款、净支付、票款差额、付款人、经办人、报账收款人、学号／工号或身份、对公／对私、开支说明、F／O／P编号、源文件和待核事项。只保留本任务有用的字段，不强制把全部字段挤在一个打印页面。

`check_ledger.py`的输入是供计算的JSON，不替代最终Excel。金额以元为单位，用最多两位小数字符串，避免二进制浮点误差。`refunds[].verified`仅在已经人工核对退款凭证时设为true，未核实退款不扣减净支付。

以下为虚构演示数据，不对应真实交易。

```json
{
  "items": [
    {
      "id": "E01",
      "name": "物料制作",
      "invoice_amount": "100.00",
      "invoices": [{"number": "DEMO-INVOICE-001", "source": "发票.pdf"}],
      "orders": ["订单截图.jpg"],
      "payments": [{"amount": "120.00", "transaction_id": "DEMO-PAYMENT-001", "source": "付款.jpg"}],
      "refunds": [{"amount": "20.00", "transaction_id": "DEMO-REFUND-001", "source": "退款.jpg", "verified": true}]
    }
  ],
  "expected_invoice_total": "100.00",
  "expected_net_payment_total": "100.00"
}
```

执行：

```bash
<python> scripts/check_ledger.py ledger.json --output ledger-check.json
```

脚本检查十进制加减、汇总及可见字段，列出差额、重复标识和缺失的来源标记。缺付款记录时净支付显示未知，而非默认为零。缺退款核实标记时不扣减并发出提示。出现非法金额或结构错误退出码为2；只有差额、重复或缺项提示时退出码仍为0。退出码0不表示材料齐备、票据真实或可报销。

## 归档结构与命名

按实际材料设置，不必创建空目录：

```text
活动名称_报销材料全套_YYYYMMDD/
  00_阅读说明.txt
  00_文件清单与命名对照.csv
  01_汇编与表格/
  02_发票原件/                  F01_事项_金额_开票YYYYMMDD.pdf
  03_订单记录原图/              O01_事项_订单_实付金额.jpg
  04_支付及退款记录原图/        P01_事项_付款人_金额_支付YYYYMMDD.png
  05_活动及报销参考资料/        指引、行动指南、真实推文截图和链接
  90_历史稿件与原始说明/        原策划、情况说明、旧清单、历史成果
```

文件名中只写已经确认的日期、金额、姓名和数量。不能用开票日期冒充付款日期，不能将付款截图中的金额改名为发票额。退款文件名宜注明原付、退款和净额。避免“最终最终版”、无意义哈希名和重复副本后缀。

原始材料是否全部纳入，以当前授权范围的源文件清单逐一对照；不等同于把活动工作目录下全部人员数据压缩。不要附带学生信息表、报名数据、缓存或与报账无关的聊天材料。

## 打包脚本

`package_packet.py`按显式文件映射复制、命名和打包，不修改源文件内容，也不自动推断最新版本。先由代理完成版本确认、业务分类和凭证去重。

```json
{
  "name": "示例活动_报销材料全套_20261101",
  "instructions": "请优先使用01目录中的最新版成果。历史稿仅供备查。",
  "files": [
    {
      "source": "/absolute/path/汇编.docx",
      "path": "01_汇编与表格/01_示例活动_报销材料汇编.docx",
      "category": "最新版成果",
      "source_label": "原目录/汇编.docx",
      "note": "按用户指定版本归档"
    }
  ]
}
```

`source`为绝对路径，或相对清单JSON的路径；`path`为包内相对路径。`source_label`用于清单展示，省略时使用源文件名，避免把本机绝对路径写入交付物。

```bash
<python> scripts/package_packet.py manifest.json --output-dir /absolute/output --dry-run
<python> scripts/package_packet.py manifest.json --output-dir /absolute/output
```

脚本拒绝路径穿越、不适合跨平台的文件名、重名及覆盖现有目录／ZIP，生成阅读说明、带SHA256的新旧命名对照和包含单一根目录的ZIP，再逐文件校验。源文件应在本次归档范围中，脚本不会自行搜索或扩大范围。

运行环境有文档／表格依赖加载器时，使用其返回的Python运行时；这两个脚本需要Python 3.11或更新版本，仅依赖Python标准库。脚本产生的核验JSON和输入清单是内部文件，除非用户需要，不纳入最终报账包。
