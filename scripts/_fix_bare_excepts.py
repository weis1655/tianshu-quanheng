#!/usr/bin/env python3
"""Fix bare except in scripts/ - T-022/CQ-007"""
import os

files_to_fix = {
    '回头看.py': [
        ('                except Exception:\n                    pass', '                except Exception as e:\n                    plog("WARNING", f"[回头看] 池代码读取失败: {e}，跳过")'),
        ('            except Exception:\n                pass', '            except Exception as e:\n                plog("WARNING", f"[回头看] 阻塞列表解析失败: {e}，跳过")'),
    ],
    'ha_monitor.py': [
        ('                        except Exception:\n                            pass', '                        except Exception as e:\n                            plog("WARNING", f"[HA] 告警发送失败: {e}，忽略")'),
    ],
    'ops_daily_report.py': [
        ('        except Exception:\n            pass', '        except Exception as e:\n            plog("WARNING", f"[Ops] metrics 解析失败: {e}，跳过")'),
    ],
    'dq_daily_report.py': [
        ('        except Exception:\n            lines.append("（历史数据解析失败）")', '        except Exception as e:\n            lines.append("（历史数据解析失败）")\n            plog("WARNING", f"[DQ] 历史数据解析失败: {e}")'),
    ],
    'dq_scanner.py': [
        ('    except Exception:\n        pass', '    except Exception as e:\n        plog("WARNING", f"[DQScanner] 数据写入失败: {e}，忽略")'),
    ],
    'pool_price_refresh.py': [
        ('    except Exception:\n        pass', '    except Exception as e:\n        plog("WARNING", f"[PriceRefresh] 价格刷新失败: {e}，忽略")'),
    ],
    'auto_heal.py': [
        ('        except Exception:\n            pass', '        except Exception as e:\n            plog("WARNING", f"[AutoHeal] 操作失败: {e}，跳过")'),
        ('        except Exception:\n            required_env_vars.append("OPENAI_API_KEY")', '        except Exception as e:\n            plog("WARNING", f"[AutoHeal] 环境变量检查失败: {e}")\n            required_env_vars.append("OPENAI_API_KEY")'),
    ],
    'data_pipeline_audit.py': [
        ('        except Exception:\n            pass', '        except Exception as e:\n            plog("WARNING", f"[Audit] 审计解析失败: {e}，跳过")'),
    ],
    'data_pipeline_validate.py': [
        ('        except Exception:\n            pass', '        except Exception as e:\n            plog("WARNING", f"[Validate] 校验失败: {e}，跳过")'),
    ],
}

for fname, fixes in files_to_fix.items():
    path = fname
    if not os.path.exists(path):
        print(f'SKIP: {fname} not found')
        continue
    content = open(path, 'r', encoding='utf-8').read()
    changed = False
    for old, new in fixes:
        if old in content:
            content = content.replace(old, new, 1)
            changed = True
            print(f'FIXED: {fname} → 1 replacement')
        else:
            print(f'NOT FOUND: {fname}')
    if changed:
        open(path, 'w', encoding='utf-8').write(content)
        print(f'DONE: {fname}')
    else:
        print(f'NO CHANGE: {fname}')
