package com.nessaj.quant.scada.auth.mapper;

import com.nessaj.quant.scada.auth.dto.WatchlistItemVO;
import com.nessaj.quant.scada.auth.entity.UserWatchlist;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface UserWatchlistMapper {
    int insert(UserWatchlist watchlist);

    int deleteById(@Param("id") Long id, @Param("userId") Long userId);

    UserWatchlist findByUserAndCode(@Param("userId") Long userId, @Param("stockCode") String stockCode);

    List<WatchlistItemVO> findPageByUser(@Param("userId") Long userId,
                                          @Param("keyword") String keyword,
                                          @Param("offset") int offset,
                                          @Param("size") int size);

    int countByUser(@Param("userId") Long userId, @Param("keyword") String keyword);

    int updateSortOrder(@Param("id") Long id, @Param("sortOrder") Integer sortOrder);

    Integer maxSortOrder(@Param("userId") Long userId);
}
