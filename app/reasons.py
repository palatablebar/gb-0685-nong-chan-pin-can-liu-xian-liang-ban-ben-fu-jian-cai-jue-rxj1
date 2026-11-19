REASONS = {
    'WIDTH':'列数不齐', 'MISSING':'缺少必填值', 'NUMBER':'数值无效',
    'DATE':'日期无效', 'UNIT':'单位未知', 'CONFLICT':'同版记录冲突',
    'VERSION':'版本不适用', 'REFERENCE':'关联记录缺失', 'RANGE':'区间无效',
    'CONSTRAINT':'约束矛盾', 'METHOD':'检测方法不适用', 'STALE':'复核版本已更新',
    'IDENTITY':'编号与样本/项目身份不符'
}

# 补发编号裁决：同编号同身份锚点才允许沿用，锚点不一致即编号冒用
IDENTITY_ANCHOR = ('sample_id', 'batch', 'product', 'analyte')
IDENTITY_DECISIONS = ('沿用原编号', '编号冒用隔离')
