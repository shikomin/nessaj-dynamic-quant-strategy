package com.nessaj.quant.scada.auth.mapper;

import com.nessaj.quant.scada.auth.entity.User;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface UserMapper {
    User findByUsername(String username);

    User findById(Long id);

    int insert(User user);
}
