package com.nessaj.quant.scada.auth.mapper;

import com.nessaj.quant.scada.auth.dto.StockSearchVO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface StockBaseInfoMapper {
    List<StockSearchVO> search(@Param("keyword") String keyword,
                               @Param("subMarket") String subMarket,
                               @Param("limit") int limit);
}
