"""Invoice page -> finance register. Preserve raw evidence; never use fuel estimates."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parents[1]

def finance_dir():
    return Path(os.environ.get('FINANCE_DATA_DIR') or ROOT/'outputs'/'finance')

def master_path(mode, directory=None):
    if mode not in {'real','simulated'}:raise ValueError('数据性质无效')
    return Path(directory or finance_dir())/('finance_master_'+mode+'.xlsx')

@contextmanager
def _lock(directory):
    directory.mkdir(parents=True,exist_ok=True);path=directory/'.finance.lock'
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('财务主表正在写入，请稍后重试')
    try:yield
    finally:os.close(fd);path.unlink(missing_ok=True)

def register_invoice(record,image_bytes,filename,*,directory=None):
    from tools.invoice_to_master import prepare,write_master
    from openpyxl import load_workbook
    if not isinstance(record,dict):raise ValueError('缺少发票识别结果')
    if not isinstance(image_bytes,bytes) or not image_bytes:raise ValueError('缺少原始发票图片')
    # The OCR kernel supplies this boolean; callers cannot omit its provenance.
    if type(record.get('mock')) is not bool:raise ValueError('缺少识别来源标记，不能认定为真实发票')
    mode='simulated' if record['mock'] else 'real'
    data=dict(record);data.pop('raw',None)
    _,check=prepare([data],mode,'upload')
    if check['rejected']:raise ValueError(check['rejected'][0]['reason'])
    directory=Path(directory or finance_dir());path=master_path(mode,directory)
    suffix=Path(filename).suffix.lower()
    if suffix not in {'.jpg','.jpeg','.png','.webp','.gif'}:raise ValueError('不支持的票图格式')
    digest=hashlib.sha256(image_bytes).hexdigest()
    evidence=directory/'evidence'/mode/(digest+suffix)
    with _lock(directory):
        evidence.parent.mkdir(parents=True,exist_ok=True)
        if not evidence.exists():evidence.write_bytes(image_bytes)
        data['source_image']=str(evidence.resolve())
        if not path.exists():
            template=ROOT/'docs'/'finance'/'项目财务主表_费用明细版.xlsx'
            if template.exists():
                wb=load_workbook(template);ws=wb['财务主表']
                for row in ws.iter_rows(min_row=2):
                    for cell in row:cell.value=None
                wb.save(path)
        report=write_master(path,[data],mode)
    return dict(report,source_mode=mode,master_path=str(path))

def finance_download(mode):
    path=master_path(mode)
    if not path.is_file():raise ValueError('尚无财务主表，请先登记')
    return path.read_bytes()
