/* Small dependency-free XLSX writer: text is always stored as text, never formula. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.LedgerXlsx=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const encoder=new TextEncoder();
  const escape=s=>String(s??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
  const declaration='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
  function column(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
  const table=new Uint32Array(256);
  for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;table[n]=c>>>0;}
  function crc(bytes){let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
  function zip(files){
    const locals=[],centrals=[];let offset=0,centralSize=0;
    for(const [name,text] of files){
      const filename=encoder.encode(name),data=typeof text==='string'?encoder.encode(text):text,checksum=crc(data);
      const local=new Uint8Array(30+filename.length+data.length),v=new DataView(local.buffer);
      v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint32(14,checksum,true);v.setUint32(18,data.length,true);v.setUint32(22,data.length,true);v.setUint16(26,filename.length,true);local.set(filename,30);local.set(data,30+filename.length);locals.push(local);
      const central=new Uint8Array(46+filename.length),w=new DataView(central.buffer);
      w.setUint32(0,0x02014b50,true);w.setUint16(4,20,true);w.setUint16(6,20,true);w.setUint16(8,0x800,true);w.setUint32(16,checksum,true);w.setUint32(20,data.length,true);w.setUint32(24,data.length,true);w.setUint16(28,filename.length,true);w.setUint32(42,offset,true);central.set(filename,46);centrals.push(central);centralSize+=central.length;offset+=local.length;
    }
    const end=new Uint8Array(22),v=new DataView(end.buffer);v.setUint32(0,0x06054b50,true);v.setUint16(8,files.length,true);v.setUint16(10,files.length,true);v.setUint32(12,centralSize,true);v.setUint32(16,offset,true);
    const out=new Uint8Array(offset+centralSize+end.length);let p=0;for(const chunk of [...locals,...centrals,end]){out.set(chunk,p);p+=chunk.length;}return out;
  }
  function sheetXML(sheet){
    const lastCol=column(Math.max(...sheet.rows.map(r=>r.length),1)-1),lastRow=Math.max(sheet.rows.length,1),header=sheet.headerRow||1;
    const rows=sheet.rows.map((row,r)=>`<row r="${r+1}" ht="${r+1===header?30:23}" customHeight="1">`+row.map((value,c)=>{
      const address=column(c)+(r+1),isNumber=typeof value==='number'||(value&&typeof value==='object'&&'number'in value);
      const number=typeof value==='number'?String(value):isNumber?value.number:null;
      if(isNumber && !/^[+-]?\d+(?:\.\d+)?$/.test(number))throw new Error('导出数值格式不正确');
      const style=r+1===header?1:isNumber?(typeof value==='number'?3:2):0;
      return isNumber?`<c r="${address}" s="${style}"><v>${number}</v></c>`:`<c r="${address}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${escape(value)}</t></is></c>`;
    }).join('')+'</row>').join('');
    return declaration+'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'+`<dimension ref="A1:${lastCol}${lastRow}"/><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="${header}" topLeftCell="A${header+1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>`+(sheet.widths||[]).map((width,c)=>`<col min="${c+1}" max="${c+1}" width="${width}" customWidth="1"/>`).join('')+`</cols><sheetData>${rows}</sheetData>`+(lastRow>header?`<autoFilter ref="A${header}:${lastCol}${lastRow}"/>`:'')+'<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/></worksheet>';
  }
  function create(sheets){
    const main='http://schemas.openxmlformats.org/spreadsheetml/2006/main',rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    const files=[
      ['[Content_Types].xml',declaration+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')+'</Types>'],
      ['_rels/.rels',declaration+`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
      ['xl/workbook.xml',declaration+`<workbook xmlns="${main}" xmlns:r="${rel}"><bookViews><workbookView/></bookViews><sheets>`+sheets.map((s,i)=>`<sheet name="${escape(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')+'</sheets></workbook>'],
      ['xl/_rels/workbook.xml.rels',declaration+'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="${rel}/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')+`<Relationship Id="rId${sheets.length+1}" Type="${rel}/styles" Target="styles.xml"/></Relationships>`],
      ['xl/styles.xml',declaration+`<styleSheet xmlns="${main}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.000"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FF263731"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFB63730"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="center"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"><alignment vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="right" vertical="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`],
      ...sheets.map((s,i)=>[`xl/worksheets/sheet${i+1}.xml`,sheetXML(s)])
    ];return zip(files);
  }
  return {create};
});
