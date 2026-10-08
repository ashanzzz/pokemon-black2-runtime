with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# Update pollRadar to auto-inspect player tile if nothing selected
old_poll_radar = """    if (loading) loading.style.display = 'none';
    renderMultiLayerGrid(data);
  } catch (e) {
    if (loading) {"""

new_poll_radar = """    if (loading) loading.style.display = 'none';
    renderMultiLayerGrid(data);

    // 若用户尚未手动点击选点，默认自动将瓦片档案卡对齐到主角当前站位瓦片
    if (!currentSelectedCell && livePlayerGrid) {
      const pKey = `${livePlayerGrid.x},${livePlayerGrid.z},${livePlayerGrid.y ?? 0}`;
      const pCell = activeCellDataMap.get(pKey);
      if (pCell) {
        renderTileInspection(pKey, livePlayerGrid.x, livePlayerGrid.y ?? 0, livePlayerGrid.z, 'P', pCell.walkable, pCell.kind || '主角站位');
      }
    }
  } catch (e) {
    if (loading) {"""

if "if (!currentSelectedCell && livePlayerGrid)" not in text:
    text = text.replace(old_poll_radar, new_poll_radar)
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added auto-inspect of player tile in pollRadar")
else:
    print("Already present")
